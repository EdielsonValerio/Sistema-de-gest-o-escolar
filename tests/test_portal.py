"""Portal do responsável: login por CPF, convite, bloqueio, revogação imediata, isolamento entre responsáveis/escolas,
permissões financeiro/pedagógico, boletim, financeiro, documentos e telas da equipe."""
from datetime import timedelta

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import RegistroAuditoria
from apps.billing.models import Cobranca
from apps.documents.models import DocumentoEmitido, ModeloDocumento
from apps.enrollments.services import criar_matricula
from apps.portal import services as sv
from apps.portal.models import AcessoAluno, AcessoResponsavel, ConfiguracaoPortal, DocumentoPortal
from apps.students.models import AlunoResponsavelFinanceiro, Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_turma_oficial, criar_usuario
from .test_cobranca_bancaria import Base as BancoBase

CPF1, CPF2, CPF3 = "529.982.247-25", "111.444.777-35", "390.533.447-05"
SENHA = "Uma-senha-bem-forte-2027!"
HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=HASH)
class PBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        self.adm = criar_usuario(self.a, "Administrador")
        self.sec = criar_usuario(self.a, "Secretaria")
        self.coord = criar_usuario(self.a, "Coordenador")
        self.cfg = ConfiguracaoPortal.obter(self.a)
        self.cfg.ativo = True
        self.cfg.save()
        self.cfg_b = ConfiguracaoPortal.obter(self.b)
        self.cfg_b.ativo = True
        self.cfg_b.save()
        self.al1 = criar_aluno(self.a, self.sec, nome="Filho Um")
        self.al2 = criar_aluno(self.a, self.sec, nome="Filho Dois")
        self.r1 = self.resp(self.a, "Resp Um", CPF1, self.al1)
        self.r2 = self.resp(self.a, "Resp Dois", CPF2, self.al2)
        self.ac1 = self.acesso(self.r1)
        self.ac2 = self.acesso(self.r2)

    def resp(self, inst, nome, cpf, aluno):
        r = Responsavel.objects.create(instituicao=inst, nome=nome, documento=cpf, email_cobranca="r@exemplo.com")
        AlunoResponsavelFinanceiro.objects.create(aluno=aluno, responsavel=r, parentesco="MAE")
        return r

    def acesso(self, resp, senha=SENHA, liberado=True):
        a = sv.criar_acesso(resp, self.adm)
        a.liberado = liberado
        a.definir_senha(senha)
        a.save()
        return a

    def entrar(self, acesso, cfg=None, senha=SENHA):
        c = Client()
        cfg = cfg or ConfiguracaoPortal.obter(acesso.instituicao)
        r = c.post(reverse("portal:login", args=[cfg.slug]), {"cpf": acesso.login, "senha": senha})
        return c, r

    def staff(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s["escola_id"] = user.perfil.instituicao_id
        s.save()
        return c


def sem_sessao(teste, c, url=None):
    """Depois de revogar/sair: nenhuma página interna abre (login ou 404), nunca 200."""
    r = c.get(url or reverse("portal:inicio"))
    teste.assertIn(r.status_code, (302, 404))
    if r.status_code == 302:
        teste.assertIn("/portal/", r.url)
        teste.assertNotIn("/portal/alunos", r.url)


class LoginTests(PBase):
    def test_login_por_cpf_e_senha(self):
        c, r = self.entrar(self.ac1)
        self.assertRedirects(r, reverse("portal:inicio"))
        self.assertContains(c.get(reverse("portal:inicio")), "Filho Um")

    def test_credencial_errada_mensagem_generica_e_igual_para_cpf_inexistente(self):
        slug = self.cfg.slug
        r1 = Client().post(reverse("portal:login", args=[slug]), {"cpf": self.ac1.login, "senha": "errada-errada-1"})
        r2 = Client().post(reverse("portal:login", args=[slug]), {"cpf": "00000000191", "senha": "errada-errada-1"})
        self.assertEqual(r1.status_code, r2.status_code)
        self.assertContains(r1, sv.ERRO_LOGIN)
        self.assertContains(r2, sv.ERRO_LOGIN)

    def test_bloqueio_apos_varias_falhas_mesmo_com_senha_certa(self):
        for _ in range(8):
            Client().post(reverse("portal:login", args=[self.cfg.slug]), {"cpf": self.ac1.login, "senha": "x" * 14})
        c, r = self.entrar(self.ac1)
        self.assertNotEqual(r.status_code, 302)
        self.assertNotIn("portal_acesso", c.session)

    def test_nao_liberado_nao_entra(self):
        self.ac1.liberado = False
        self.ac1.save()
        c, r = self.entrar(self.ac1)
        self.assertNotEqual(r.status_code, 302)

    def test_portal_desativado_nao_entra(self):
        self.cfg.ativo = False
        self.cfg.save()
        c, r = self.entrar(self.ac1)
        self.assertNotEqual(r.status_code, 302)

    def test_sem_senha_nao_entra(self):
        self.ac1.senha_hash = ""
        self.ac1.save()
        c, r = self.entrar(self.ac1, senha="")
        self.assertNotEqual(r.status_code, 302)

    def test_cpf_de_uma_escola_nao_entra_no_link_de_outra(self):
        c = Client()
        r = c.post(reverse("portal:login", args=[self.cfg_b.slug]), {"cpf": self.ac1.login, "senha": SENHA})
        self.assertNotEqual(r.status_code, 302)

    def test_slug_inexistente_404(self):
        self.assertEqual(Client().get(reverse("portal:login", args=["nao-existe"])).status_code, 404)

    def test_sem_sessao_telas_internas_404(self):
        for nome, args in (("portal:inicio", []), ("portal:aluno", [self.al1.pk]), ("portal:documentos", [])):
            self.assertEqual(Client().get(reverse(nome, args=args)).status_code, 404)

    def test_sair_encerra_sessao(self):
        c, _ = self.entrar(self.ac1)
        c.post(reverse("portal:sair"))
        sem_sessao(self, c)

    def test_sessao_do_portal_nao_abre_telas_da_equipe(self):
        c, _ = self.entrar(self.ac1)
        for url in (reverse("portal:config"), reverse("portal:acesso", args=[self.r1.pk]), "/alunos/"):
            r = c.get(url)
            self.assertIn(r.status_code, (302, 403, 404))
            if r.status_code == 302:
                self.assertIn("entrar", r.url)

    def test_login_do_portal_nao_vira_usuario_da_equipe(self):
        c, _ = self.entrar(self.ac1)
        self.assertNotIn("_auth_user_id", c.session)


class ConviteTests(PBase):
    def novo(self):
        r = Responsavel.objects.create(instituicao=self.a, nome="Novo Resp", documento=CPF3, email_cobranca="n@e.com")
        a = sv.criar_acesso(r, self.adm)
        a.liberado = True
        a.save()
        return a

    def test_convite_define_senha_uso_unico(self):
        a = self.novo()
        token = a.gerar_convite()
        url = reverse("portal:convite", args=[token])
        r = Client().post(url, {"senha1": SENHA, "senha2": SENHA})
        self.assertEqual(r.status_code, 302)
        a.refresh_from_db()
        self.assertTrue(a.tem_senha)
        self.assertEqual(Client().get(url).status_code, 404)  # já usado

    def test_token_nao_fica_em_claro_no_banco(self):
        a = self.novo()
        token = a.gerar_convite()
        a.refresh_from_db()
        self.assertNotIn(token, a.convite_hash)
        self.assertEqual(len(a.convite_hash), 64)

    def test_convite_expirado(self):
        a = self.novo()
        token = a.gerar_convite()
        AcessoResponsavel.objects.filter(pk=a.pk).update(convite_expira=timezone.now() - timedelta(minutes=1))
        self.assertEqual(Client().get(reverse("portal:convite", args=[token])).status_code, 404)

    def test_convite_falso(self):
        self.assertEqual(Client().get(reverse("portal:convite", args=["x" * 40])).status_code, 404)

    def test_senha_fraca_recusada(self):
        a = self.novo()
        token = a.gerar_convite()
        for fraca in ("curta1!", "123456789012", "password1234", CPF3.replace(".", "").replace("-", "") + "ab"):
            r = Client().post(reverse("portal:convite", args=[token]), {"senha1": fraca, "senha2": fraca})
            self.assertEqual(r.status_code, 200, fraca)
        a.refresh_from_db()
        self.assertFalse(a.tem_senha)

    def test_senhas_diferentes_recusadas(self):
        a = self.novo()
        token = a.gerar_convite()
        r = Client().post(reverse("portal:convite", args=[token]), {"senha1": SENHA, "senha2": SENHA + "x"})
        self.assertEqual(r.status_code, 200)

    def test_convite_de_acesso_nao_liberado_invalido(self):
        a = self.novo()
        token = a.gerar_convite()
        a.liberado = False
        a.save()
        self.assertEqual(Client().get(reverse("portal:convite", args=[token])).status_code, 404)

    def test_cpf_invalido_nao_cria_acesso(self):
        from django.core.exceptions import ValidationError
        r = Responsavel.objects.create(instituicao=self.a, nome="CPF ruim", documento="111.111.111-11")
        with self.assertRaises(ValidationError):
            sv.criar_acesso(r, self.adm)
        r2 = Responsavel.objects.create(instituicao=self.a, nome="Sem CPF")
        with self.assertRaises(ValidationError):
            sv.criar_acesso(r2, self.adm)


class RevogacaoTests(PBase):
    def test_revogar_derruba_sessao_na_hora(self):
        c, _ = self.entrar(self.ac1)
        self.assertEqual(c.get(reverse("portal:inicio")).status_code, 200)
        self.ac1.liberado = False
        self.ac1.save()
        sem_sessao(self, c)

    def test_desativar_portal_derruba_sessao(self):
        c, _ = self.entrar(self.ac1)
        self.cfg.ativo = False
        self.cfg.save()
        sem_sessao(self, c)

    def test_trocar_senha_derruba_sessoes_antigas(self):
        c, _ = self.entrar(self.ac1)
        self.ac1.definir_senha("Outra-senha-forte-2028!")
        self.ac1.save()
        sem_sessao(self, c)

    def test_responsavel_inativo_nao_entra(self):
        c, _ = self.entrar(self.ac1)
        Responsavel.objects.filter(pk=self.r1.pk).update(ativo=False)
        sem_sessao(self, c)


class IsolamentoTests(PBase):
    def test_idor_entre_responsaveis(self):
        AcessoAluno.objects.filter(acesso=self.ac1).update(pedagogico=True)
        c, _ = self.entrar(self.ac1)
        for nome in ("portal:aluno", "portal:boletim", "portal:financeiro"):
            self.assertEqual(c.get(reverse(nome, args=[self.al1.pk])).status_code, 200, nome)
            self.assertEqual(c.get(reverse(nome, args=[self.al2.pk])).status_code, 404, nome)

    def test_aluno_de_outra_escola_404(self):
        al_b = criar_aluno(self.b, criar_usuario(self.b, "Secretaria"), nome="Aluno B")
        c, _ = self.entrar(self.ac1)
        self.assertEqual(c.get(reverse("portal:aluno", args=[al_b.pk])).status_code, 404)

    def test_vinculo_cruzado_de_outra_escola_nao_da_acesso(self):
        al_b = criar_aluno(self.b, criar_usuario(self.b, "Secretaria"), nome="Aluno B")
        AcessoAluno.objects.create(instituicao=self.a, acesso=self.ac1, aluno=al_b)  # dado corrompido
        c, _ = self.entrar(self.ac1)
        self.assertEqual(c.get(reverse("portal:aluno", args=[al_b.pk])).status_code, 404)

    def test_id_nao_numerico_ou_inexistente(self):
        c, _ = self.entrar(self.ac1)
        self.assertEqual(c.get(reverse("portal:aluno", args=[999999])).status_code, 404)

    def test_paginas_sem_cache(self):
        c, _ = self.entrar(self.ac1)
        self.assertIn("no-store", c.get(reverse("portal:inicio"))["Cache-Control"])


class PermissoesTests(PBase):
    def test_flags_financeiro_e_pedagogico(self):
        v = AcessoAluno.objects.get(acesso=self.ac1, aluno=self.al1)
        c, _ = self.entrar(self.ac1)
        # padrão: financeiro sim, pedagógico não
        self.assertEqual(c.get(reverse("portal:financeiro", args=[self.al1.pk])).status_code, 200)
        self.assertEqual(c.get(reverse("portal:aluno", args=[self.al1.pk])).status_code, 200)  # dados básicos
        self.assertEqual(c.get(reverse("portal:boletim", args=[self.al1.pk])).status_code, 404)
        v.financeiro, v.pedagogico = False, True
        v.save()
        self.assertEqual(c.get(reverse("portal:financeiro", args=[self.al1.pk])).status_code, 404)
        self.assertEqual(c.get(reverse("portal:aluno", args=[self.al1.pk])).status_code, 200)
        self.assertEqual(c.get(reverse("portal:boletim", args=[self.al1.pk])).status_code, 200)

    def test_sincronizar_nao_sobrescreve_ajuste_da_escola(self):
        v = AcessoAluno.objects.get(acesso=self.ac1, aluno=self.al1)
        v.financeiro, v.pedagogico = False, True
        v.save()
        sv.sincronizar_alunos(self.ac1)
        v.refresh_from_db()
        self.assertFalse(v.financeiro)
        self.assertTrue(v.pedagogico)


class FinanceiroPortalTests(BancoBase):
    """Parcelas/boletos: só o que está em nome do responsável logado."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.cfg_p = ConfiguracaoPortal.obter(self.inst)
        self.cfg_p.ativo = True
        self.cfg_p.save()
        AlunoResponsavelFinanceiro.objects.create(aluno=self.aluno, responsavel=self.resp, parentesco="MAE")
        self.ac = sv.criar_acesso(self.resp, self.adm)
        self.ac.liberado = True
        self.ac.definir_senha(SENHA)
        self.ac.save()
        self.ativar()

    def entrar(self, ac):
        c = Client()
        r = c.post(reverse("portal:login", args=[ConfiguracaoPortal.obter(ac.instituicao).slug]),
                   {"cpf": ac.login, "senha": SENHA})
        self.assertEqual(r.status_code, 302)
        return c

    def test_parcelas_e_boleto_registrado(self):
        ct = self.contratar()
        c = self.entrar(self.ac)
        p = ct.parcelas.order_by("numero").first()
        r = c.get(reverse("portal:financeiro", args=[self.aluno.pk]))
        self.assertEqual(r.status_code, 200)
        cob = Cobranca.objects.filter(parcela=p, eh_teste=False).first()
        if cob and cob.status == Cobranca.Status.PENDENTE:
            r = c.get(reverse("portal:boleto", args=[self.aluno.pk, p.pk]))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(RegistroAuditoria.objects.filter(acao="PORTAL_BOLETO_VISTO").exists())

    def test_boleto_de_parcela_de_outro_responsavel_404(self):
        outro = Responsavel.objects.create(instituicao=self.inst, nome="Outro Pagador", documento="111.444.777-35")
        ct = self.contratar(resp=outro)
        p = ct.parcelas.first()
        c = self.entrar(self.ac)
        self.assertEqual(c.get(reverse("portal:boleto", args=[self.aluno.pk, p.pk])).status_code, 404)
        r = c.get(reverse("portal:financeiro", args=[self.aluno.pk]))
        self.assertNotContains(r, f"/financeiro/{p.pk}/boleto/")

    def test_boleto_de_teste_ou_sem_cobranca_404(self):
        ct = self.contratar()
        p = ct.parcelas.first()
        Cobranca.objects.filter(parcela=p).update(eh_teste=True)
        c = self.entrar(self.ac)
        self.assertEqual(c.get(reverse("portal:boleto", args=[self.aluno.pk, p.pk])).status_code, 404)

    def test_boleto_com_acesso_sem_flag_financeiro_404(self):
        ct = self.contratar()
        p = ct.parcelas.first()
        AcessoAluno.objects.filter(acesso=self.ac).update(financeiro=False)
        c = self.entrar(self.ac)
        self.assertEqual(c.get(reverse("portal:boleto", args=[self.aluno.pk, p.pk])).status_code, 404)


class DocumentosPortalTests(PBase):
    def modelo(self, **kw):
        d = dict(instituicao=self.a, nome="Declaração de matrícula", tipo="DECL_MATRICULA", destinatario="ALUNO",
                 conteudo="<p>Declaramos que [nomeAluno] é aluno.</p>", disponivel_portal=True)
        d.update(kw)
        return ModeloDocumento.objects.create(**d)

    def aluno_ped(self):
        AcessoAluno.objects.filter(acesso=self.ac1).update(pedagogico=True)

    def test_so_aparece_modelo_marcado_para_o_portal(self):
        self.modelo()
        self.modelo(nome="Interno", disponivel_portal=False)
        c, _ = self.entrar(self.ac1)
        r = c.get(reverse("portal:documentos"))
        self.assertContains(r, "Declaração de matrícula")
        self.assertNotContains(r, "Interno")

    def test_gerar_e_reabrir_proprio_documento(self):
        m = self.modelo()
        c, _ = self.entrar(self.ac1)
        r = c.post(reverse("portal:documentos"), {"modelo": m.pk, "aluno": self.al1.pk})
        self.assertEqual(r.status_code, 302)
        dp = DocumentoPortal.objects.get(acesso=self.ac1)
        self.assertContains(c.get(reverse("portal:documento", args=[dp.documento_id])), "Filho Um")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="PORTAL_DOCUMENTO_GERADO").exists())

    def test_nao_gera_para_aluno_de_outro_responsavel(self):
        m = self.modelo()
        c, _ = self.entrar(self.ac1)
        r = c.post(reverse("portal:documentos"), {"modelo": m.pk, "aluno": self.al2.pk})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(DocumentoPortal.objects.exists())

    def test_nao_abre_documento_de_outro_responsavel(self):
        m = self.modelo()
        c2, _ = self.entrar(self.ac2)
        c2.post(reverse("portal:documentos"), {"modelo": m.pk, "aluno": self.al2.pk})
        dp = DocumentoPortal.objects.get(acesso=self.ac2)
        c1, _ = self.entrar(self.ac1)
        self.assertEqual(c1.get(reverse("portal:documento", args=[dp.documento_id])).status_code, 404)

    def test_modelo_nao_marcado_ou_de_outra_escola_404(self):
        m = self.modelo(disponivel_portal=False)
        mb = ModeloDocumento.objects.create(instituicao=self.b, nome="B", tipo="DECL_MATRICULA", destinatario="ALUNO",
                                            conteudo="<p>x</p>", disponivel_portal=True)
        c, _ = self.entrar(self.ac1)
        for pk in (m.pk, mb.pk):
            self.assertEqual(c.post(reverse("portal:documentos"), {"modelo": pk, "aluno": self.al1.pk}).status_code, 404)

    def test_limite_por_hora(self):
        m = self.modelo()
        c, _ = self.entrar(self.ac1)
        for _ in range(21):
            c.post(reverse("portal:documentos"), {"modelo": m.pk, "aluno": self.al1.pk})
        self.assertEqual(DocumentoPortal.objects.filter(acesso=self.ac1).count(), 20)

    def test_modelo_boletim_nao_pode_ser_marcado_para_portal(self):
        from apps.documents.forms import ModeloDocumentoForm
        f = ModeloDocumentoForm(data={"nome": "Bol", "tipo": "BOLETIM", "destinatario": "ALUNO", "conteudo": "<p>x</p>",
                                      "ativo": "on", "disponivel_portal": "on"}, instituicao=self.a)
        self.assertFalse(f.is_valid())


class GestaoTests(PBase):
    def test_secretaria_libera_e_revoga_pela_ficha(self):
        r = Responsavel.objects.create(instituicao=self.a, nome="Fulana", documento=CPF3, email_cobranca="f@e.com")
        c = self.staff(self.sec)
        url = reverse("portal:acesso", args=[r.pk])
        self.assertEqual(c.get(url).status_code, 200)
        c.post(url, {"acao": "criar"})
        ac = AcessoResponsavel.objects.get(responsavel=r)
        self.assertFalse(ac.liberado)
        c.post(url, {"acao": "liberar"})
        ac.refresh_from_db()
        self.assertTrue(ac.liberado)
        c.post(url, {"acao": "revogar"})
        ac.refresh_from_db()
        self.assertFalse(ac.liberado)
        acoes = set(RegistroAuditoria.objects.values_list("acao", flat=True))
        self.assertTrue({"PORTAL_ACESSO_LIBERADO", "PORTAL_ACESSO_REVOGADO"} & acoes or any(a.startswith("PORTAL_") for a in acoes))

    def test_convite_mostra_link_copiavel(self):
        c = self.staff(self.sec)
        self.r1.email_cobranca = ""
        self.r1.save()
        r = c.post(reverse("portal:acesso", args=[self.r1.pk]), {"acao": "convite"}, follow=True)
        self.assertContains(r, "/portal/convite/")  # sem e-mail: link aparece para a escola copiar
        self.r1.email_cobranca = "r@exemplo.com"
        self.r1.save()
        r = c.post(reverse("portal:acesso", args=[self.r1.pk]), {"acao": "convite"}, follow=True)
        self.assertContains(r, "Convite enviado")

    def test_salvar_permissoes_por_aluno(self):
        v = AcessoAluno.objects.get(acesso=self.ac1, aluno=self.al1)
        c = self.staff(self.sec)
        c.post(reverse("portal:acesso", args=[self.r1.pk]), {"acao": "alunos", f"ped_{v.pk}": "on"})
        v.refresh_from_db()
        self.assertFalse(v.financeiro)
        self.assertTrue(v.pedagogico)

    def test_adicionar_aluno_de_outra_escola_nao_funciona(self):
        al_b = criar_aluno(self.b, criar_usuario(self.b, "Secretaria"), nome="Aluno B")
        c = self.staff(self.sec)
        c.post(reverse("portal:acesso", args=[self.r1.pk]), {"acao": "adicionar", "codigo": al_b.codigo})
        self.assertFalse(AcessoAluno.objects.filter(acesso=self.ac1, aluno=al_b).exists())

    def test_responsavel_de_outra_escola_404(self):
        rb = Responsavel.objects.create(instituicao=self.b, nome="RB", documento=CPF3)
        self.assertEqual(self.staff(self.sec).get(reverse("portal:acesso", args=[rb.pk])).status_code, 404)

    def test_permissoes_da_equipe(self):
        url = reverse("portal:acesso", args=[self.r1.pk])
        self.assertEqual(self.staff(self.coord).get(url).status_code, 403)
        self.assertEqual(self.staff(self.coord).get(reverse("portal:config")).status_code, 403)
        self.assertEqual(self.staff(self.sec).get(reverse("portal:config")).status_code, 403)  # só administrador
        self.assertEqual(self.staff(self.adm).get(reverse("portal:config")).status_code, 200)
        self.assertEqual(Client().get(url).status_code, 302)

    def test_admin_ativa_e_desativa_portal(self):
        c = self.staff(self.adm)
        c.post(reverse("portal:config"), {"acao": "desativar"})
        self.cfg.refresh_from_db()
        self.assertFalse(self.cfg.ativo)
        c.post(reverse("portal:config"), {"acao": "ativar"})
        self.cfg.refresh_from_db()
        self.assertTrue(self.cfg.ativo)

    def test_redefinir_apaga_senha_e_derruba_sessao(self):
        c, _ = self.entrar(self.ac1)
        self.staff(self.sec).post(reverse("portal:acesso", args=[self.r1.pk]), {"acao": "redefinir"})
        self.ac1.refresh_from_db()
        self.assertFalse(self.ac1.tem_senha)
        sem_sessao(self, c)
