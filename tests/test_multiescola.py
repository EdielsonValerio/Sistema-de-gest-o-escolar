import io
import tempfile
from datetime import date

from django.apps import apps
from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.academic.models import SerieOfertada, SerieReferencia
from apps.accounts.models import PerfilUsuario
from apps.accounts.permissions import sincronizar_perfis
from apps.audit.models import RegistroAuditoria
from apps.core.models import Instituicao
from apps.core.tenancy import SESSAO_ESCOLA, login_do_usuario
from apps.core.tenant import APPS_DE_NEGOCIO, GLOBAIS, EscolaIncompativel, eh_modelo_de_escola
from apps.finance.models import AjusteParcela
from apps.students.models import AlunoResponsavelFinanceiro, Responsavel

from .helpers import (criar_aluno, criar_ano, criar_instituicao, criar_plano, criar_turma_oficial,
                      criar_usuario)

SENHA = "senha-de-teste-123"


def png(cor="red", tamanho=(30, 20), formato="PNG"):
    buf = io.BytesIO()
    Image.new("RGB", tamanho, cor).save(buf, formato)
    return buf.getvalue()


def usuario_da_escola(inst, login, grupo="Administrador"):
    u = User.objects.create_user(f"{inst.pk}.{login}", password=SENHA)
    PerfilUsuario.objects.create(user=u, instituicao=inst, login=login)
    u.groups.add(Group.objects.get(name=grupo))
    return u


class BaseTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.a = criar_instituicao("Escola Alfa")
        cls.b = criar_instituicao("Escola Beta")
        cls.sistema = User.objects.create_superuser("root", password=SENHA)  # sem escola

    def entrar(self, escola, login, senha=SENHA):
        return self.client.post(reverse("accounts:login"), {"escola": escola.pk if escola else "", "usuario": login,
                                                            "senha": senha})


class LoginPorEscolaTests(BaseTest):
    def test_login_exige_escola_usuario_e_senha(self):
        usuario_da_escola(self.a, "maria")
        r = self.entrar(self.a, "maria")
        self.assertRedirects(r, reverse("core:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSAO_ESCOLA], self.a.pk)

    def test_mesma_mensagem_para_qualquer_falha(self):
        usuario_da_escola(self.a, "maria")
        msgs = set()
        for escola, login, senha in [(self.a, "maria", "errada"), (self.a, "inexistente", SENHA),
                                     (self.b, "maria", SENHA)]:
            r = self.entrar(escola, login, senha)
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("_auth_user_id", self.client.session)
            msgs.add(r.context["form"].non_field_errors()[0])
        self.assertEqual(len(msgs), 1)

    def test_escola_obrigatoria(self):
        usuario_da_escola(self.a, "maria")
        r = self.entrar(None, "maria")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_usuario_de_uma_escola_nao_entra_em_outra(self):
        usuario_da_escola(self.a, "maria")
        self.entrar(self.b, "maria")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_mesmo_login_em_escolas_diferentes_sao_pessoas_diferentes(self):
        ua, ub = usuario_da_escola(self.a, "secretaria"), usuario_da_escola(self.b, "secretaria")
        ub.set_password("outra-senha-forte-9"); ub.save()
        self.entrar(self.a, "SECRETARIA")  # sem diferença de maiúsculas
        self.assertEqual(int(self.client.session["_auth_user_id"]), ua.pk)
        self.client.logout()
        self.entrar(self.b, "secretaria", "outra-senha-forte-9")
        self.assertEqual(int(self.client.session["_auth_user_id"]), ub.pk)
        self.client.logout()
        self.entrar(self.b, "secretaria", SENHA)  # senha da outra escola não vale
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_de_exibicao_nao_mostra_prefixo_interno(self):
        u = usuario_da_escola(self.a, "maria")
        self.assertEqual(login_do_usuario(u), "maria")
        self.entrar(self.a, "maria")
        self.assertContains(self.client.get(reverse("core:dashboard")), "maria")
        self.assertNotContains(self.client.get(reverse("core:dashboard")), f"{self.a.pk}.maria")

    def test_escola_inativa_nao_aparece_nem_permite_login(self):
        usuario_da_escola(self.a, "maria")
        self.b.ativa = False
        self.b.save()
        usuario_da_escola(self.b, "joao")
        pagina = self.client.get(reverse("accounts:login"))
        self.assertContains(pagina, "Escola Alfa")
        self.assertNotContains(pagina, "Escola Beta")
        self.entrar(self.b, "joao")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_inativar_escola_derruba_sessoes_abertas(self):
        usuario_da_escola(self.a, "maria")
        self.entrar(self.a, "maria")
        self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
        Instituicao.objects.filter(pk=self.a.pk).update(ativa=False)
        r = self.client.get(reverse("core:dashboard"))
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_sessao_de_outra_escola_e_encerrada(self):
        usuario_da_escola(self.a, "maria")
        self.entrar(self.a, "maria")
        s = self.client.session
        s[SESSAO_ESCOLA] = self.b.pk
        s.save()
        r = self.client.get(reverse("core:dashboard"))
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_usuario_inativo_nao_entra(self):
        u = usuario_da_escola(self.a, "maria")
        u.is_active = False
        u.save()
        self.entrar(self.a, "maria")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_administrador_do_sistema_entra_pelo_modo_sistema(self):
        r = self.client.post(reverse("accounts:login"), {"sistema": "1", "usuario": "root", "senha": SENHA})
        self.assertRedirects(r, reverse("core:escola_lista"), fetch_redirect_response=False)
        self.assertEqual(self.client.session[SESSAO_ESCOLA], 0)

    def test_usuario_de_escola_nao_entra_pelo_modo_sistema(self):
        u = usuario_da_escola(self.a, "maria")
        self.client.post(reverse("accounts:login"), {"sistema": "1", "usuario": u.username, "senha": SENHA})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_superusuario_nao_entra_numa_escola_que_nao_e_a_dele(self):
        self.entrar(self.a, "root")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_externo_e_ignorado(self):
        usuario_da_escola(self.a, "maria")
        r = self.client.post(reverse("accounts:login"), {"escola": self.a.pk, "usuario": "maria", "senha": SENHA,
                                                         "next": "https://evil.example/"})
        self.assertEqual(r.url, reverse("core:dashboard"))

    def test_login_pagina_mostra_logotipo_da_escola(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.a.logotipo.save("x.png", SimpleUploadedFile("x.png", png()), save=True)
            self.assertContains(self.client.get(reverse("accounts:login")), self.a.logotipo.url)


class AreaDoSistemaTests(BaseTest):
    def dados(self, **kw):
        d = {"nome": "Colégio Gama", "cnpj": "", "ativa": "on", "nome_": "x", "login": "diretor", "senha1": "Senha-Forte-2026",
             "senha2": "Senha-Forte-2026"}
        d.pop("nome_")
        d.update(kw)
        return d

    def post_nova(self, **kw):
        # o formulário do administrador usa o prefixo "admin-" (os dois formulários têm um campo "nome")
        d = self.dados(**kw)
        return self.client.post(reverse("core:escola_nova"), {
            "nome": d["nome"], "cnpj": d["cnpj"], "ativa": d.get("ativa", ""), "logotipo": d.get("logotipo", ""),
            "admin-nome": "Maria Diretora", "admin-login": d["login"], "admin-senha1": d["senha1"],
            "admin-senha2": d["senha2"], "admin-sobrenome": "", "admin-email": d.get("email", "")})

    def test_somente_superusuario_acessa(self):
        adm = usuario_da_escola(self.a, "maria")
        self.client.force_login(adm)
        for nome, args in [("escola_lista", []), ("escola_nova", []), ("escola_editar", [self.a.pk]),
                           ("escola_admin_novo", [self.a.pk])]:
            self.assertEqual(self.client.get(reverse(f"core:{nome}", args=args)).status_code, 403, nome)
        self.assertEqual(self.client.post(reverse("core:escola_nova"), {"nome": "X"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("core:escola_lista")).status_code, 302)

    def test_menu_do_administrador_do_sistema_so_tem_area_do_sistema(self):
        self.client.force_login(self.sistema)
        r = self.client.get(reverse("core:escola_lista"))
        self.assertContains(r, reverse("core:escola_nova"))
        self.assertNotContains(r, reverse("students:aluno_lista"))
        self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("core:escola_lista"),
                             fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("students:aluno_lista")).status_code, 403)
        self.assertEqual(self.client.get(reverse("finance:caixa")).status_code, 403)

    def test_cadastro_cria_escola_configuracao_e_administrador(self):
        self.client.force_login(self.sistema)
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            d = self.dados(cnpj="11.222.333/0001-81")
            r = self.client.post(reverse("core:escola_nova"), {
                "nome": d["nome"], "cnpj": d["cnpj"], "ativa": "on", "logotipo": SimpleUploadedFile("l.png", png()),
                "admin-nome": "Maria Diretora", "admin-login": "diretor", "admin-senha1": d["senha1"],
                "admin-senha2": d["senha2"], "admin-sobrenome": "", "admin-email": ""})
            self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
            escola = Instituicao.objects.get(nome="Colégio Gama")
            self.assertTrue(escola.logotipo.name.startswith("escolas/logotipos/"))
            self.assertTrue(hasattr(escola, "configuracao"))
            adm = User.objects.get(perfil__instituicao=escola)
            self.assertEqual(adm.perfil.login, "diretor")
            self.assertEqual(escola.nome, "Colégio Gama")  # o nome do administrador não sobrescreve o da escola
            self.assertEqual(adm.first_name, "Maria Diretora")
            self.assertEqual(list(adm.groups.values_list("name", flat=True)), ["Administrador"])
            self.assertFalse(adm.is_staff or adm.is_superuser)
            self.assertTrue(RegistroAuditoria.objects.filter(instituicao=escola, acao="ESCOLA_CADASTRADA").exists())
            # o administrador consegue entrar e vê o logotipo da própria escola
            self.client.logout()
            self.entrar(escola, "diretor", d["senha1"])
            # primeiro acesso: antes de usar o sistema o administrador cria a própria senha
            self.assertRedirects(self.client.get(reverse("core:dashboard")), reverse("accounts:senha_trocar"),
                                 fetch_redirect_response=False)
            nova = "SenhaPropria#4471x"
            self.client.post(reverse("accounts:senha_trocar"),
                             {"senha_atual": d["senha1"], "senha1": nova, "senha2": nova})
            pagina = self.client.get(reverse("core:dashboard"))
            self.assertContains(pagina, escola.logotipo.url)
            self.assertContains(pagina, "Colégio Gama")

    def test_cadastro_e_atomico(self):
        self.client.force_login(self.sistema)
        r = self.post_nova(senha2="diferente")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Instituicao.objects.filter(nome="Colégio Gama").exists())
        r = self.post_nova(senha1="123", senha2="123")  # senha fraca
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Instituicao.objects.filter(nome="Colégio Gama").exists())

    def test_validacoes_da_escola(self):
        self.client.force_login(self.sistema)
        self.assertEqual(self.post_nova(nome="escola alfa").status_code, 200)  # duplicada sem diferenciar caixa
        self.assertEqual(Instituicao.objects.filter(nome__iexact="escola alfa").count(), 1)
        self.assertEqual(self.post_nova(cnpj="11.111.111/1111-11").status_code, 200)
        self.assertEqual(self.post_nova(login="a b").status_code, 200)
        self.assertEqual(self.post_nova(login="ab").status_code, 200)
        self.assertFalse(Instituicao.objects.filter(nome="Colégio Gama").exists())

    def test_logotipo_invalido_rejeitado(self):
        self.client.force_login(self.sistema)
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            for arq in (SimpleUploadedFile("x.png", b"nao e imagem", "image/png"),
                        SimpleUploadedFile("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>"),
                        SimpleUploadedFile("x.gif", png(formato="GIF"))):
                r = self.client.post(reverse("core:escola_nova"), {
                    "nome": "Colégio Gama", "logotipo": arq, "login": "diretor", "senha1": "Senha-Forte-2026",
                    "senha2": "Senha-Forte-2026"})
                self.assertEqual(r.status_code, 200)
            self.assertFalse(Instituicao.objects.filter(nome="Colégio Gama").exists())

    def test_editar_escola_e_inativar(self):
        self.client.force_login(self.sistema)
        adm = usuario_da_escola(self.a, "maria")
        r = self.client.post(reverse("core:escola_editar", args=[self.a.pk]), {"nome": "Escola Alfa 2", "cnpj": ""})
        self.assertEqual(r.status_code, 302)
        self.a.refresh_from_db()
        self.assertEqual((self.a.nome, self.a.ativa), ("Escola Alfa 2", False))
        self.assertTrue(RegistroAuditoria.objects.filter(instituicao=self.a, acao="ESCOLA_ALTERADA").exists())
        self.assertContains(self.client.get(reverse("core:escola_editar", args=[self.a.pk])), "maria")

    def test_novo_administrador_e_redefinir_senha(self):
        self.client.force_login(self.sistema)
        adm = usuario_da_escola(self.a, "maria")
        r = self.client.post(reverse("core:escola_admin_novo", args=[self.a.pk]), {
            "nome": "Pedro", "login": "pedro", "senha1": "Outra-Senha-2026", "senha2": "Outra-Senha-2026"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(PerfilUsuario.objects.filter(instituicao=self.a, login="pedro").exists())
        r = self.client.post(reverse("core:escola_admin_novo", args=[self.a.pk]), {  # login repetido na escola
            "nome": "Pedro", "login": "MARIA", "senha1": "Outra-Senha-2026", "senha2": "Outra-Senha-2026"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("core:escola_admin_senha", args=[self.a.pk, adm.pk]),
                             {"senha1": "Nova-Senha-Forte-77", "senha2": "Nova-Senha-Forte-77"})
        self.assertEqual(r.status_code, 302)
        adm.refresh_from_db()
        self.assertTrue(adm.check_password("Nova-Senha-Forte-77"))
        # usuário de outra escola não pode ser alcançado pela URL de uma escola diferente
        ub = usuario_da_escola(self.b, "bia")
        self.assertEqual(self.client.get(reverse("core:escola_admin_senha", args=[self.a.pk, ub.pk])).status_code, 404)


class UsuariosDaEscolaTests(BaseTest):
    def test_login_unico_so_dentro_da_escola(self):
        adm_a = usuario_da_escola(self.a, "admin")
        usuario_da_escola(self.b, "secretaria")
        self.client.force_login(adm_a)
        perfil = Group.objects.get(name="Secretaria").pk
        dados = {"username": "secretaria", "first_name": "Ana", "perfil": perfil, "is_active": "on",
                 "senha1": "Senha-Forte-2026", "senha2": "Senha-Forte-2026"}
        r = self.client.post(reverse("accounts:usuario_novo"), dados)  # existe na escola B, mas não na A
        self.assertEqual(r.status_code, 302)
        novo = PerfilUsuario.objects.get(instituicao=self.a, login="secretaria")
        self.assertEqual(novo.user.username, f"{self.a.pk}.secretaria")
        self.assertEqual(self.client.post(reverse("accounts:usuario_novo"), dados).status_code, 200)  # agora duplica

    def test_administrador_da_escola_so_ve_e_edita_usuarios_da_propria_escola(self):
        adm_a = usuario_da_escola(self.a, "admin")
        ub = usuario_da_escola(self.b, "bia")
        self.client.force_login(adm_a)
        pagina = self.client.get(reverse("accounts:usuario_lista"))
        self.assertContains(pagina, "admin")
        self.assertNotContains(pagina, "bia")
        self.assertEqual(self.client.get(reverse("accounts:usuario_editar", args=[ub.pk])).status_code, 404)

    def test_administrador_da_escola_nao_gerencia_superusuario(self):
        adm_a = usuario_da_escola(self.a, "admin")
        su = User.objects.create_superuser("chefe", password=SENHA)
        PerfilUsuario.objects.create(user=su, instituicao=self.a, login="chefe")
        self.client.force_login(adm_a)
        self.assertNotContains(self.client.get(reverse("accounts:usuario_lista")), "chefe")
        self.assertEqual(self.client.get(reverse("accounts:usuario_editar", args=[su.pk])).status_code, 404)

    def test_perfil_administrador_nao_tem_permissoes_do_sistema(self):
        g = Group.objects.get(name="Administrador")
        codigos = set(g.permissions.values_list("codename", flat=True))
        self.assertNotIn("add_instituicao", codigos)
        self.assertNotIn("change_instituicao", codigos)
        self.assertNotIn("change_seriereferencia", codigos)
        self.assertIn("view_seriereferencia", codigos)
        self.assertIn("change_aluno", codigos)

    def test_serie_de_referencia_so_o_administrador_do_sistema_altera(self):
        adm_a = usuario_da_escola(self.a, "admin")
        SerieReferencia.objects.create(codigo="TESTE-01", nome="Série teste", etapa="EF1", ordem=1)
        ref = SerieReferencia.objects.get(codigo="TESTE-01")
        self.client.force_login(adm_a)
        self.assertEqual(self.client.get(reverse("academic:serie_ref_lista")).status_code, 200)  # leitura ok
        self.assertEqual(self.client.get(reverse("academic:serie_ref_editar", args=[ref.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("academic:serie_ref_nova")).status_code, 403)
        self.client.force_login(self.sistema)
        self.assertEqual(self.client.get(reverse("academic:serie_ref_editar", args=[ref.pk])).status_code, 200)
        r = self.client.post(reverse("academic:serie_ref_editar", args=[ref.pk]), {
            "codigo": "TESTE-01", "nome": "Série teste 2", "etapa": ref.etapa, "ordem": 1, "ativa": "on"})
        self.assertEqual(r.status_code, 302)
        ref.refresh_from_db()
        self.assertEqual(ref.nome, "Série teste 2")


class IsolamentoDeDadosTests(BaseTest):
    def test_todo_modelo_de_negocio_tem_escola_ou_esta_na_lista_de_compartilhados(self):
        """Se alguém criar uma tabela nova sem `instituicao`, este teste falha e obriga a decidir."""
        sem_escola = []
        for m in apps.get_models():
            if m._meta.app_label not in APPS_DE_NEGOCIO or m._meta.label_lower in GLOBAIS:
                continue
            if not eh_modelo_de_escola(m):
                sem_escola.append(m._meta.label)
            else:
                campo = m._meta.get_field("instituicao")
                self.assertFalse(campo.null, f"{m._meta.label}.instituicao deve ser NOT NULL")
        self.assertEqual(sem_escola, [])

    def test_nao_relaciona_aluno_de_uma_escola_com_responsavel_de_outra(self):
        u = criar_usuario(self.a, "Administrador")
        aluno = criar_aluno(self.a, u)
        resp_b = Responsavel.objects.create(instituicao=self.b, nome="Resp B")
        with self.assertRaises(EscolaIncompativel):
            AlunoResponsavelFinanceiro.objects.create(aluno=aluno, responsavel=resp_b, parentesco="MAE")
        with self.assertRaises(EscolaIncompativel):  # também quando só o id é informado
            AlunoResponsavelFinanceiro.objects.create(aluno_id=aluno.pk, responsavel_id=resp_b.pk, parentesco="MAE")
        self.assertFalse(AlunoResponsavelFinanceiro.objects.exists())

    def test_escola_e_herdada_do_registro_pai(self):
        u = criar_usuario(self.a, "Administrador")
        aluno = criar_aluno(self.a, u)
        resp = Responsavel.objects.create(instituicao=self.a, nome="Resp A")
        v = AlunoResponsavelFinanceiro.objects.create(aluno=aluno, responsavel=resp, parentesco="MAE")
        self.assertEqual(v.instituicao_id, self.a.pk)
        ano = criar_ano(self.a)
        ref = SerieReferencia.objects.create(codigo="X-01", nome="X", etapa="EF1", ordem=1)
        so = SerieOfertada.objects.create(ano_letivo=ano, serie_referencia=ref)
        self.assertEqual(so.instituicao_id, self.a.pk)

    def test_matricula_nao_mistura_aluno_e_turma_de_escolas_diferentes(self):
        from apps.enrollments.models import Matricula
        ua, ub = criar_usuario(self.a, "Administrador"), criar_usuario(self.b, "Administrador")
        aluno_a = criar_aluno(self.a, ua)
        turma_b = criar_turma_oficial(self.b)
        with self.assertRaises(EscolaIncompativel):
            Matricula.objects.create(instituicao=self.a, aluno=aluno_a, turma=turma_b, tipo="OFICIAL",
                                     ano_letivo=turma_b.ano_letivo, data_ingresso=date(2027, 2, 1))

    def test_instituicao_nao_pode_faltar(self):
        from apps.students.models import Aluno
        with self.assertRaises(Exception):
            Aluno.objects.create(nome="Sem escola", data_nascimento=date(2015, 1, 1), codigo="X")

    def test_registros_financeiros_dependentes_herdam_a_escola(self):
        import uuid
        from apps.enrollments.services import criar_matricula
        from apps.finance import services as fin
        from apps.finance.models import AlocacaoRecebimento, Estorno
        u = criar_usuario(self.a, "Financeiro")
        turma = criar_turma_oficial(self.a)
        aluno = criar_aluno(self.a, u)
        resp = Responsavel.objects.create(instituicao=self.a, nome="R")
        m = criar_matricula(aluno=aluno, turma=turma, usuario=u)
        c = fin.contratar_plano(matricula=m, plano=criar_plano(self.a, turma), responsavel=resp, usuario=u)
        p = c.parcelas.order_by("numero").first()
        fin.aplicar_desconto(p, "10.00", u, "bolsa")
        rec, _ = fin.registrar_recebimento(aluno=aluno, itens=[(p.pk, "490.00")], forma="PIX", valor_recebido=None,
                                           usuario=u, token=uuid.uuid4())
        fin.estornar_recebimento(rec, u, "lançamento errado")
        for modelo in (AjusteParcela, AlocacaoRecebimento, Estorno):
            objs = list(modelo.objects.all())
            self.assertTrue(objs, modelo.__name__)
            self.assertTrue(all(o.instituicao_id == self.a.pk for o in objs), modelo.__name__)
        self.assertTrue(all(pc.instituicao_id == self.a.pk for pc in c.parcelas.all()))

    def test_parcela_nao_aceita_responsavel_de_outra_escola(self):
        from apps.enrollments.services import criar_matricula
        from apps.finance import services as fin
        from apps.finance.models import Parcela
        u = criar_usuario(self.a, "Financeiro")
        turma = criar_turma_oficial(self.a)
        aluno = criar_aluno(self.a, u)
        resp_a = Responsavel.objects.create(instituicao=self.a, nome="RA")
        resp_b = Responsavel.objects.create(instituicao=self.b, nome="RB")
        m = criar_matricula(aluno=aluno, turma=turma, usuario=u)
        c = fin.contratar_plano(matricula=m, plano=criar_plano(self.a, turma), responsavel=resp_a, usuario=u)
        p = c.parcelas.first()
        p.responsavel = resp_b
        with self.assertRaises(EscolaIncompativel):
            p.save()

    def test_usuario_de_uma_escola_nao_enxerga_dados_de_outra(self):
        ua, ub = usuario_da_escola(self.a, "admin"), usuario_da_escola(self.b, "admin")
        aluno_b = criar_aluno(self.b, ub, nome="Aluno da Beta")
        self.client.force_login(ua)
        self.assertEqual(self.client.get(reverse("students:aluno_ficha", args=[aluno_b.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse("students:aluno_lista")), "Aluno da Beta")
        self.assertNotContains(self.client.get(reverse("students:aluno_lista"), {"q": "Beta"}), "Aluno da Beta")
        self.assertEqual(self.client.get(reverse("students:aluno_editar", args=[aluno_b.pk])).status_code, 404)

    def test_codigos_de_aluno_sao_independentes_por_escola(self):
        ua, ub = criar_usuario(self.a, "Administrador"), criar_usuario(self.b, "Administrador")
        a1, b1 = criar_aluno(self.a, ua), criar_aluno(self.b, ub)
        self.assertEqual(a1.codigo, b1.codigo)  # cada escola tem a própria sequência
        self.assertNotEqual(a1.instituicao_id, b1.instituicao_id)

    def test_fotos_e_logotipos_ficam_em_pastas_por_escola(self):
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            u = criar_usuario(self.a, "Administrador")
            aluno = criar_aluno(self.a, u)
            aluno.foto.save("minha foto.png", SimpleUploadedFile("f.png", png()), save=True)
            self.assertTrue(aluno.foto.name.startswith(f"alunos/{self.a.pk}/"))
            self.assertNotIn("minha", aluno.foto.name)
