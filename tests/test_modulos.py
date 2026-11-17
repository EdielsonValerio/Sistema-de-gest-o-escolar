"""Controle de módulos por escola: ficha da escola (administrador do sistema), bloqueio de telas/permissões/menu,
efeitos em matrícula, cobrança, portal, pré-matrícula e comunicados; dados preservados ao desligar."""
import io
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import RegistroAuditoria
from apps.core import modulos
from apps.core.models import Instituicao
from apps.enrollments.models import Matricula
from apps.finance import services as fin
from apps.finance.models import Parcela
from apps.portal import services as psv
from apps.portal.models import AcessoAluno, ConfiguracaoPortal
from apps.students.models import AlunoResponsavelFinanceiro, LinkPreMatricula, Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_plano, criar_turma_oficial, criar_usuario

HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SENHA = "Uma-senha-bem-forte-2027!"


@override_settings(PASSWORD_HASHERS=HASH)
class Base(TestCase):
    def setUp(self):
        self.inst = criar_instituicao("Escola M")  # helper habilita todos
        self.sis = User.objects.create_superuser("sis", password="x-senha-longa-123")
        self.adm = criar_usuario(self.inst, "Administrador")
        self.sec = criar_usuario(self.inst, "Secretaria")
        self.fin = criar_usuario(self.inst, "Financeiro")

    def mods(self, *cods, inst=None):
        inst = inst or self.inst
        inst.modulos = list(cods)
        inst.save(update_fields=["modulos"])
        return inst

    def cli(self, user):
        c = Client()
        c.force_login(user)
        if hasattr(user, "perfil"):
            s = c.session
            s["escola_id"] = user.perfil.instituicao_id
            s.save()
        return c


class RegistroTests(TestCase):
    def test_dependencias_e_normalizacao(self):
        self.assertEqual(modulos.dependencias_faltando(["cobranca"]), [("cobranca", "financeiro")])
        self.assertEqual(modulos.dependencias_faltando(["cobranca", "financeiro"]), [])
        self.assertEqual(modulos.normalizar(["portal", "xx", "diario", "portal"]), ["diario", "portal"])

    def test_rotas_e_permissoes_mapeadas(self):
        self.assertEqual(modulos.modulo_da_rota("finance", "parcela_lista"), "financeiro")
        self.assertEqual(modulos.modulo_da_rota("payables", "x"), "financeiro")
        self.assertEqual(modulos.modulo_da_rota("billing", "cobranca_lista"), "cobranca")
        self.assertIsNone(modulos.modulo_da_rota("billing", "webhook"))  # banco avisa pagamento: nunca bloquear
        self.assertEqual(modulos.modulo_da_rota("diario", "painel"), "diario")
        self.assertEqual(modulos.modulo_da_rota("portal", "config"), "portal")
        self.assertIsNone(modulos.modulo_da_rota("portal", "inicio"))  # portal público decide sozinho
        self.assertEqual(modulos.modulo_da_rota("students", "pre_matricula_config"), "prematricula")
        self.assertIsNone(modulos.modulo_da_rota("students", "pre_matricula_publica"))
        self.assertIsNone(modulos.modulo_da_rota("students", "aluno_lista"))  # base
        self.assertEqual(modulos.modulo_da_permissao("students", "change_linkprematricula"), "prematricula")
        self.assertIsNone(modulos.modulo_da_permissao("students", "view_aluno"))


class FichaDaEscolaTests(Base):
    def dados(self, **extra):
        return {"nome": self.inst.nome, "cnpj": "", "ativa": "on", **extra}

    def test_so_administrador_do_sistema_altera(self):
        url = reverse("core:escola_editar", args=[self.inst.pk])
        self.assertEqual(self.cli(self.adm).get(url).status_code, 403)
        self.assertEqual(self.cli(self.adm).post(url, self.dados(mod_diario="on")).status_code, 403)

    def test_tela_lista_os_modulos_e_salva_com_auditoria(self):
        self.mods()
        c = self.cli(self.sis)
        url = reverse("core:escola_editar", args=[self.inst.pk])
        r = c.get(url)
        for cod in modulos.TODOS:
            self.assertContains(r, f'name="mod_{cod}"')
        c.post(url, self.dados(mod_diario="on", mod_financeiro="on", mod_cobranca="on"))
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.modulos, ["diario", "financeiro", "cobranca"])
        ev = RegistroAuditoria.objects.get(acao="MODULOS_ALTERADOS")
        self.assertEqual(ev.dados["antes"], [])
        self.assertEqual(ev.dados["depois"], ["diario", "financeiro", "cobranca"])
        c.post(url, self.dados(mod_diario="on", mod_financeiro="on", mod_cobranca="on"))  # sem mudança
        self.assertEqual(RegistroAuditoria.objects.filter(acao="MODULOS_ALTERADOS").count(), 1)

    def test_cobranca_sem_financeiro_e_recusada(self):
        self.mods("financeiro")
        c = self.cli(self.sis)
        r = c.post(reverse("core:escola_editar", args=[self.inst.pk]), self.dados(mod_cobranca="on"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "habilite também")
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.modulos, ["financeiro"])

    def test_nova_escola_comeca_so_com_a_base_e_com_modulos_escolhidos(self):
        c = self.cli(self.sis)
        base = {"nome": "Escola Nova", "cnpj": "", "ativa": "on", "admin-nome": "Ana", "admin-login": "ana.adm",
                "admin-senha1": "Senha-Provisoria-9x!", "admin-senha2": "Senha-Provisoria-9x!"}
        r = c.post(reverse("core:escola_nova"), {**base, "mod_portal": "on"})
        e = Instituicao.objects.get(nome="Escola Nova")
        self.assertEqual(e.modulos, ["portal"])
        r = c.post(reverse("core:escola_nova"), {**base, "nome": "Escola Sem Modulos", "admin-login": "outro.adm"})
        self.assertEqual(Instituicao.objects.get(nome="Escola Sem Modulos").modulos, [])

    def test_escola_sem_modulo_marcado_fica_so_com_a_base(self):
        self.mods("diario")
        self.cli(self.sis).post(reverse("core:escola_editar", args=[self.inst.pk]), self.dados())
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.modulos, [])


class BloqueioTests(Base):
    def test_base_sempre_funciona_sem_nenhum_modulo(self):
        self.mods()
        c = self.cli(self.sec)
        self.assertEqual(c.get(reverse("students:aluno_lista")).status_code, 200)
        self.assertEqual(c.get(reverse("academic:turma_lista")).status_code, 200)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 200)

    def test_tela_financeira_bloqueada_e_liberada(self):
        c = self.cli(self.fin)
        url = reverse("finance:plano_lista")
        self.assertEqual(c.get(url).status_code, 200)
        self.mods("diario")
        r = self.cli(self.fin).get(url)
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "Módulo não contratado", status_code=403)
        self.mods("financeiro")
        self.assertEqual(self.cli(self.fin).get(url).status_code, 200)

    def test_permissoes_e_menu_acompanham_os_modulos(self):
        self.mods("diario")
        u = User.objects.get(pk=self.fin.pk)
        self.assertFalse(u.has_perm("finance.view_parcela"))
        self.assertFalse(u.has_perm("payables.view_contapagar"))
        sec = User.objects.get(pk=self.sec.pk)
        self.assertTrue(sec.has_perm("students.view_aluno"))
        self.assertFalse(sec.has_perm("students.change_linkprematricula"))
        html = self.cli(self.adm).get(reverse("core:dashboard")).content.decode()
        self.assertNotIn(reverse("finance:plano_lista"), html)
        self.assertNotIn(reverse("billing:cobranca_lista"), html)
        self.assertNotIn(reverse("portal:config"), html)
        self.assertNotIn(reverse("comunicacao:lista"), html)
        self.mods(*modulos.TODOS)
        html = self.cli(self.adm).get(reverse("core:dashboard")).content.decode()
        self.assertIn(reverse("finance:plano_lista"), html)
        self.assertIn(reverse("portal:config"), html)

    def test_diario_bloqueado_para_professor_e_coordenador(self):
        prof = criar_usuario(self.inst, "Professor")
        coord = criar_usuario(self.inst, "Coordenador")
        self.mods("financeiro")
        self.assertEqual(self.cli(prof).get(reverse("diario:painel")).status_code, 403)
        self.assertEqual(self.cli(coord).get(reverse("diario:coordenacao")).status_code, 403)
        self.mods("diario")
        self.assertEqual(self.cli(prof).get(reverse("diario:painel")).status_code, 200)

    def test_pre_matricula_config_e_publica(self):
        link = LinkPreMatricula.objects.create(instituicao=self.inst, ativo=True)
        pub = reverse("students:pre_matricula_publica", args=[link.token])
        self.assertEqual(Client().get(pub).status_code, 200)
        self.mods()
        self.assertEqual(Client().get(pub).status_code, 404)
        self.assertEqual(self.cli(self.adm).get(reverse("students:pre_matricula_config")).status_code, 403)

    def test_whatsapp_e_comunicados(self):
        self.mods()
        self.assertEqual(self.cli(self.adm).get(reverse("comunicacao:config")).status_code, 403)
        from apps.comunicacao import services as csv
        with self.assertRaises(ValidationError):
            csv.criar_comunicado(instituicao=self.inst, usuario=self.adm, alvo="ESCOLA", mensagem="oi")

    def test_webhook_do_banco_nao_e_bloqueado(self):
        self.mods()
        import uuid
        r = Client().post(reverse("billing:webhook", args=[uuid.uuid4()]), "{}", content_type="application/json")
        self.assertNotContains(r, "Módulo não contratado", status_code=r.status_code)

    def test_modo_suporte_tambem_respeita_os_modulos(self):
        self.mods()
        c = self.cli(self.sis)
        s = c.session
        from apps.core.tenancy import SESSAO_SUPORTE
        s[SESSAO_SUPORTE] = self.inst.pk
        s.save()
        self.assertEqual(c.get(reverse("finance:plano_lista")).status_code, 403)


class EfeitosTests(Base):
    def setUp(self):
        super().setUp()
        self.turma = criar_turma_oficial(self.inst, numero=1)
        self.aluno = criar_aluno(self.inst, self.sec)
        self.plano = criar_plano(self.inst, self.turma)

    def test_matricula_sem_financeiro_ignora_planos(self):
        self.mods()
        m, contrato = fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec)
        self.assertIsNotNone(m.pk)
        self.assertIsNone(contrato)

    def test_matricula_com_financeiro_continua_exigindo_escolha(self):
        with self.assertRaises(ValidationError):
            fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec)

    def test_tela_de_matricula_sem_financeiro_nao_pede_plano(self):
        self.mods()
        url = reverse("enrollments:vincular", args=[self.aluno.pk]) + f"?tipo=OFICIAL&turma={self.turma.pk}"
        r = self.cli(self.sec).get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, self.plano.nome)
        self.mods(*modulos.TODOS)
        self.assertContains(self.cli(self.sec).get(url), self.plano.nome)

    def test_cobranca_bancaria_inativa_sem_o_modulo(self):
        from apps.billing import services as bs
        from apps.billing.models import ConfiguracaoBancaria
        cfg = bs.configuracao(self.inst)
        ConfiguracaoBancaria.objects.filter(pk=cfg.pk).update(status="ATIVA")
        self.mods("financeiro")
        self.assertIsNone(bs.cobranca_ativa(self.inst))

    def test_desligar_preserva_os_dados_e_religar_devolve(self):
        resp = Responsavel.objects.create(instituicao=self.inst, nome="R", documento="")
        with self.captureOnCommitCallbacks(execute=True):
            from apps.enrollments.services import criar_matricula
            mat = criar_matricula(aluno=self.aluno, turma=self.turma, usuario=self.sec)
            fin.contratar_plano(matricula=mat, plano=self.plano, responsavel=resp, usuario=self.sec)
        n = Parcela.objects.filter(instituicao=self.inst).count()
        self.assertGreater(n, 0)
        self.mods()
        self.assertEqual(Parcela.objects.filter(instituicao=self.inst).count(), n)
        self.mods("financeiro")
        self.assertEqual(self.cli(self.fin).get(reverse("finance:plano_lista")).status_code, 200)


class PortalModulosTests(Base):
    CPF = "529.982.247-25"

    def setUp(self):
        super().setUp()
        cfg = ConfiguracaoPortal.obter(self.inst)
        cfg.ativo = True
        cfg.save()
        self.cfg = cfg
        self.aluno = criar_aluno(self.inst, self.sec, nome="Filho")
        self.resp = Responsavel.objects.create(instituicao=self.inst, nome="Pai", documento=self.CPF,
                                               email_cobranca="p@e.com")
        AlunoResponsavelFinanceiro.objects.create(aluno=self.aluno, responsavel=self.resp, parentesco="PAI")
        self.ac = psv.criar_acesso(self.resp, self.adm)
        self.ac.liberado = True
        self.ac.definir_senha(SENHA)
        self.ac.save()
        AcessoAluno.objects.filter(acesso=self.ac).update(financeiro=True, pedagogico=True)

    def entrar(self):
        c = Client()
        r = c.post(reverse("portal:login", args=[self.cfg.slug]), {"cpf": self.ac.login, "senha": SENHA})
        return c, r

    def test_portal_desligado_nao_entra_e_derruba_sessao(self):
        c, r = self.entrar()
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get(reverse("portal:inicio")).status_code, 200)
        self.mods("financeiro", "diario")
        r = c.get(reverse("portal:inicio"))
        self.assertIn(r.status_code, (302, 404))
        self.assertNotEqual(self.entrar()[1].status_code, 302)
        self.assertEqual(Client().get(reverse("portal:login", args=[self.cfg.slug])).status_code, 404)

    def test_secoes_dependem_dos_modulos(self):
        self.mods("portal")
        c, _ = self.entrar()
        self.assertEqual(c.get(reverse("portal:inicio")).status_code, 200)
        self.assertEqual(c.get(reverse("portal:aluno", args=[self.aluno.pk])).status_code, 200)  # dados: base
        self.assertEqual(c.get(reverse("portal:financeiro", args=[self.aluno.pk])).status_code, 404)
        self.assertEqual(c.get(reverse("portal:boletim", args=[self.aluno.pk])).status_code, 404)
        html = c.get(reverse("portal:inicio")).content.decode()
        self.assertNotIn(reverse("portal:financeiro", args=[self.aluno.pk]), html)
        self.assertNotIn(reverse("portal:boletim", args=[self.aluno.pk]), html)
        self.mods("portal", "financeiro", "diario")
        self.assertEqual(c.get(reverse("portal:financeiro", args=[self.aluno.pk])).status_code, 200)
        self.assertEqual(c.get(reverse("portal:boletim", args=[self.aluno.pk])).status_code, 200)

    def test_boleto_exige_modulo_de_cobranca(self):
        self.mods("portal", "financeiro")
        c, _ = self.entrar()
        self.assertEqual(c.get(reverse("portal:boleto", args=[self.aluno.pk, 1])).status_code, 404)

    def test_convite_com_modulo_desligado_e_invalido(self):
        novo = Responsavel.objects.create(instituicao=self.inst, nome="Nova", documento="111.444.777-35")
        a = psv.criar_acesso(novo, self.adm)
        a.liberado = True
        a.save()
        token = a.gerar_convite()
        self.mods()
        self.assertEqual(Client().get(reverse("portal:convite", args=[token])).status_code, 404)


class ComandoTests(Base):
    def run_cmd(self, *args):
        out = io.StringIO()
        call_command("habilitar_modulos", *args, stdout=out)
        return out.getvalue()

    def test_habilitar_substituir_acrescentar_e_dependencias(self):
        self.mods()
        self.run_cmd("--escola", str(self.inst.pk), "--modulos", "diario,financeiro")
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.modulos, ["diario", "financeiro"])
        self.run_cmd("--escola", str(self.inst.pk), "--modulos", "portal", "--acrescentar")
        self.inst.refresh_from_db()
        self.assertEqual(self.inst.modulos, ["diario", "financeiro", "portal"])
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            self.run_cmd("--escola", str(self.inst.pk), "--modulos", "cobranca")
        with self.assertRaises(CommandError):
            self.run_cmd("--escola", str(self.inst.pk), "--modulos", "inexistente")

    def test_todas_as_escolas_todos_e_listar(self):
        outra = criar_instituicao("Outra")
        self.mods(inst=outra)
        self.mods()
        self.run_cmd("--todas-escolas", "--todos")
        for e in (self.inst, outra):
            e.refresh_from_db()
            self.assertEqual(e.modulos, list(modulos.TODOS))
        self.assertTrue(RegistroAuditoria.objects.filter(acao="MODULOS_ALTERADOS", instituicao=outra).exists())
        self.assertIn("Escola M", self.run_cmd("--listar"))
