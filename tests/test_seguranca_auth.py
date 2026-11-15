"""Segurança de autenticação: força bruta, senhas, ativação/recuperação por token, sessões e MFA."""
import re
import time
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import seguranca
from apps.accounts.models import MfaDispositivo, PerfilUsuario, TentativaLogin, TokenConta
from apps.accounts.permissions import sincronizar_perfis
from apps.audit.models import EventoSeguranca, RegistroAuditoria

from .helpers import criar_instituicao, criar_usuario

HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]
SENHA = "senha-de-teste-123"
NOVA = "Outra-senha-bem-longa-2026"


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.a, cls.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        cls.sec = criar_usuario(cls.a, "Secretaria", username="sec")
        cls.sec.perfil.login = "sec"
        cls.sec.perfil.save()
        cls.sec.email = "sec@x.com"
        cls.sec.save()
        cls.adm = criar_usuario(cls.a, "Administrador", username="adm")
        cls.adm.perfil.login = "adm"
        cls.adm.perfil.save()

    def login(self, c, login, senha, escola=None):
        return c.post(reverse("accounts:login"), {"escola": (escola or self.a).pk, "usuario": login, "senha": senha})


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False)
class ForcaBrutaTests(Base):
    def test_bloqueia_conta_apos_falhas_mesmo_com_senha_certa(self):
        c = Client()
        for _ in range(seguranca.MAX_FALHAS_CONTA):
            r = self.login(c, "sec", "errada")
            self.assertContains(r, "incorretos")
        r = self.login(c, "sec", SENHA)  # senha certa, mas a conta está bloqueada temporariamente
        self.assertContains(r, "Muitas tentativas")
        self.assertNotIn("_auth_user_id", c.session)
        self.assertTrue(EventoSeguranca.objects.filter(tipo="LOGIN_BLOQUEADO").exists())

    def test_bloqueio_e_temporario(self):
        c = Client()
        for _ in range(seguranca.MAX_FALHAS_CONTA):
            self.login(c, "sec", "errada")
        TentativaLogin.objects.update(criado_em=timezone.now() - timezone.timedelta(hours=5))
        r = self.login(c, "sec", SENHA)
        self.assertEqual(r.status_code, 302)

    def test_conta_inexistente_se_comporta_igual(self):
        c = Client()
        for _ in range(seguranca.MAX_FALHAS_CONTA):
            self.login(c, "fantasma", "x")
        self.assertContains(self.login(c, "fantasma", "x"), "Muitas tentativas")

    def test_bloqueio_por_ip(self):
        c = Client()
        with override_settings(LOGIN_MAX_FALHAS_IP=3):
            with mock.patch.object(seguranca, "MAX_FALHAS_IP", 3):
                for i in range(3):
                    self.login(c, f"u{i}", "x")
                self.assertContains(self.login(c, "sec", SENHA), "Muitas tentativas")

    def test_sucesso_zera_o_contador_da_conta(self):
        c = Client()
        for _ in range(seguranca.MAX_FALHAS_CONTA - 1):
            self.login(c, "sec", "errada")
        self.assertEqual(self.login(c, "sec", SENHA).status_code, 302)
        self.assertFalse(TentativaLogin.objects.filter(tipo="LOGIN_CONTA").exists())

    def test_chaves_nao_guardam_login_em_claro(self):
        self.login(Client(), "sec", "errada")
        for t in TentativaLogin.objects.all():
            self.assertNotIn("sec", t.chave)
            self.assertEqual(len(t.chave), 64)

    def test_admin_do_django_nao_e_atalho_de_login(self):
        r = Client().get("/admin/login/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/entrar/", r["Location"])


class SenhaTests(TestCase):
    def test_politica_de_senha_minimo_12_e_argon2(self):
        from django.contrib.auth.hashers import make_password
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            validate_password("curta#1A")
        with self.assertRaises(ValidationError):
            validate_password("password1234")  # comum
        validate_password("frase-longa-e-incomum-2026")
        self.assertTrue(make_password("x").startswith("argon2"))


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False, SITE_URL="https://e.exemplo.com")
class TokensTests(Base):
    def test_ativacao_por_link_nunca_envia_senha(self):
        c = Client()
        c.force_login(self.adm)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        g = Group.objects.get(name="Secretaria").pk
        r = c.post(reverse("accounts:usuario_novo"), {"username": "nova", "first_name": "Nova", "last_name": "", "email": "nova@x.com",
                                                      "perfil": g, "is_active": "on", "senha1": "", "senha2": "", "enviar_email": "on"})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(perfil__login="nova")
        self.assertFalse(u.has_usable_password())
        corpo = mail.outbox[0].body
        token = re.search(r"/ativar-conta/([\w-]+)/", corpo).group(1)
        self.assertFalse(TokenConta.objects.filter(token_hash=token).exists())  # só o hash é guardado
        c2 = Client()
        self.assertEqual(c2.get(reverse("accounts:ativar", args=[token])).status_code, 200)
        r = c2.post(reverse("accounts:ativar", args=[token]), {"senha1": NOVA, "senha2": NOVA})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.login(Client(), "nova", NOVA).status_code, 302)
        # reutilização é rejeitada
        self.assertEqual(c2.get(reverse("accounts:ativar", args=[token])).status_code, 400)
        self.assertEqual(c2.post(reverse("accounts:ativar", args=[token]), {"senha1": NOVA + "x", "senha2": NOVA + "x"}).status_code, 400)

    def test_token_expirado_e_invalido(self):
        tk = seguranca.gerar_token(self.sec, TokenConta.Finalidade.ATIVACAO)
        TokenConta.objects.update(expira_em=timezone.now() - timezone.timedelta(minutes=1))
        self.assertEqual(Client().get(reverse("accounts:ativar", args=[tk])).status_code, 400)
        self.assertEqual(Client().get(reverse("accounts:ativar", args=["lixo"])).status_code, 400)
        # token de ativação não serve para redefinição
        tk2 = seguranca.gerar_token(self.sec, TokenConta.Finalidade.ATIVACAO)
        self.assertEqual(Client().get(reverse("accounts:redefinir", args=[tk2])).status_code, 400)
        self.assertTrue(EventoSeguranca.objects.filter(tipo="TOKEN_INVALIDO").exists())

    def test_novo_token_invalida_o_anterior(self):
        t1 = seguranca.gerar_token(self.sec, TokenConta.Finalidade.RECUPERACAO)
        seguranca.gerar_token(self.sec, TokenConta.Finalidade.RECUPERACAO)
        self.assertIsNone(seguranca.token_valido(t1, TokenConta.Finalidade.RECUPERACAO))

    def test_recuperacao_resposta_igual_para_existente_e_inexistente(self):
        c = Client()
        r1 = c.post(reverse("accounts:recuperar"), {"escola": self.a.pk, "usuario": "sec"}, follow=True)
        n = len(mail.outbox)
        r2 = c.post(reverse("accounts:recuperar"), {"escola": self.a.pk, "usuario": "naoexiste"}, follow=True)
        self.assertEqual(n, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(r1.content.count(b"enviamos um link"), r2.content.count(b"enviamos um link"))
        self.assertEqual(r1.content.count(b"enviamos um link"), 1)

    def test_recuperacao_redefine_invalida_sessoes_e_avisa(self):
        c_logado = Client()
        self.assertEqual(self.login(c_logado, "sec", SENHA).status_code, 302)
        self.assertEqual(c_logado.get(reverse("core:dashboard")).status_code, 200)
        Client().post(reverse("accounts:recuperar"), {"escola": self.a.pk, "usuario": "sec"})
        token = re.search(r"/redefinir-senha/([\w-]+)/", mail.outbox[-1].body).group(1)
        r = Client().post(reverse("accounts:redefinir", args=[token]), {"senha1": NOVA, "senha2": NOVA})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c_logado.get(reverse("core:dashboard")).status_code, 302)  # sessão antiga caiu
        self.assertIn("alterada", mail.outbox[-1].subject)
        self.assertEqual(self.login(Client(), "sec", NOVA).status_code, 302)
        # redefinir não inicia sessão
        c = Client()
        c.post(reverse("accounts:recuperar"), {"escola": self.a.pk, "usuario": "sec"})
        token = re.search(r"/redefinir-senha/([\w-]+)/", mail.outbox[-1].body).group(1)
        c.post(reverse("accounts:redefinir", args=[token]), {"senha1": NOVA + "9", "senha2": NOVA + "9"})
        self.assertNotIn("_auth_user_id", c.session)

    def test_recuperacao_limitada(self):
        c = Client()
        for _ in range(seguranca.MAX_RECUPERACOES_CONTA + 2):
            c.post(reverse("accounts:recuperar"), {"escola": self.a.pk, "usuario": "sec"})
        self.assertEqual(len(mail.outbox), seguranca.MAX_RECUPERACOES_CONTA)

    def test_senha_fraca_e_recusada_na_redefinicao(self):
        tk = seguranca.gerar_token(self.sec, TokenConta.Finalidade.RECUPERACAO)
        r = Client().post(reverse("accounts:redefinir", args=[tk]), {"senha1": "curta", "senha2": "curta"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(TokenConta.objects.filter(usado_em__isnull=True).exists())


def totp_atual(usuario):
    return seguranca._codigo(seguranca._segredo(MfaDispositivo.objects.get(user=usuario)), int(time.time() // 30))


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=True)
class MfaTests(Base):
    def ativar_mfa(self, user, login):
        c = Client()
        r = self.login(c, login, SENHA)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/mfa/configurar/", r["Location"])
        self.assertEqual(c.get(reverse("accounts:mfa_configurar")).status_code, 200)
        codigo = totp_atual(user)
        r = c.post(reverse("accounts:mfa_configurar"), {"codigo": codigo})
        self.assertContains(r, "MFA ativada")
        codigos = re.findall(r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}", r.content.decode())
        return c, codigos

    def test_perfil_privilegiado_e_obrigado_a_cadastrar_mfa(self):
        c, codigos = self.ativar_mfa(self.adm, "adm")
        self.assertEqual(len(codigos), 8)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 200)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="MFA_ATIVADA").exists())
        # segredo cifrado e códigos só em hash
        d = MfaDispositivo.objects.get(user=self.adm)
        self.assertNotIn(seguranca._segredo(d), d.segredo_cifrado)
        for cod in codigos:
            self.assertNotIn(cod, str(d.codigos_recuperacao))

    def test_nao_ha_atalho_enquanto_mfa_pendente(self):
        c = Client()
        self.login(c, "adm", SENHA)
        for url in (reverse("core:dashboard"), reverse("accounts:usuario_lista"), "/alunos/"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn("/mfa/configurar/", r["Location"])

    def test_perfil_comum_nao_e_obrigado(self):
        r = self.login(Client(), "sec", SENHA)
        self.assertNotIn("/mfa/", r["Location"])

    def test_login_com_mfa_exige_codigo(self):
        self.ativar_mfa(self.adm, "adm")
        c = Client()
        r = self.login(c, "adm", SENHA)
        self.assertIn("/mfa/verificar/", r["Location"])
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 302)
        # código errado
        r = c.post(reverse("accounts:mfa_verificar"), {"codigo": "000000"})
        self.assertContains(r, "inválido")
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 302)
        # código correto (o passo atual já foi usado na ativação: replay rejeitado)
        r = c.post(reverse("accounts:mfa_verificar"), {"codigo": totp_atual(self.adm)})
        self.assertContains(r, "inválido")
        d = MfaDispositivo.objects.get(user=self.adm)
        d.ultimo_passo = 0
        d.save()
        r = c.post(reverse("accounts:mfa_verificar"), {"codigo": totp_atual(self.adm)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 200)

    def test_codigo_de_recuperacao_uso_unico(self):
        _, codigos = self.ativar_mfa(self.adm, "adm")
        c = Client()
        self.login(c, "adm", SENHA)
        r = c.post(reverse("accounts:mfa_verificar"), {"codigo": codigos[0]})
        self.assertEqual(r.status_code, 302)
        c2 = Client()
        self.login(c2, "adm", SENHA)
        self.assertContains(c2.post(reverse("accounts:mfa_verificar"), {"codigo": codigos[0]}), "inválido")

    def test_tentativas_de_mfa_sao_limitadas(self):
        self.ativar_mfa(self.adm, "adm")
        c = Client()
        self.login(c, "adm", SENHA)
        for _ in range(seguranca.MAX_FALHAS_MFA):
            c.post(reverse("accounts:mfa_verificar"), {"codigo": "111111"})
        r = c.post(reverse("accounts:mfa_verificar"), {"codigo": "222222"})
        self.assertContains(r, "Muitas tentativas")

    def test_superusuario_tambem_exige_mfa_e_admin_django_nao_contorna(self):
        root = User.objects.create_superuser("root", password=SENHA)
        c = Client()
        r = c.post(reverse("accounts:login") + "?sistema=1", {"sistema": "1", "usuario": "root", "senha": SENHA})
        self.assertIn("/mfa/configurar/", r["Location"])
        self.assertEqual(c.get("/admin/").status_code, 302)
        self.assertIn("/mfa/configurar/", c.get("/admin/")["Location"])

    def test_administrador_redefine_mfa_de_outro_usuario_da_mesma_escola(self):
        self.ativar_mfa(self.adm, "adm")
        MfaDispositivo.objects.create(user=self.sec, segredo_cifrado="x", confirmado=True)
        c = Client()
        c.force_login(self.adm)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        self.assertEqual(c.post(reverse("accounts:mfa_redefinir", args=[self.sec.pk])).status_code, 302)
        self.assertFalse(MfaDispositivo.objects.filter(user=self.sec).exists())
        outro = criar_usuario(self.b, "Administrador", username="adm_b")
        self.assertEqual(c.post(reverse("accounts:mfa_redefinir", args=[outro.pk])).status_code, 404)


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False)
class SessaoETransversalTests(Base):
    def test_logout_invalida_sessao_e_login_troca_a_chave(self):
        c = Client()
        c.get(reverse("accounts:login"))
        antes = c.session.session_key
        self.login(c, "sec", SENHA)
        self.assertNotEqual(antes, c.session.session_key)  # sem session fixation
        c.post(reverse("accounts:logout"))
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 302)

    def test_auditoria_e_eventos_sao_imutaveis(self):
        reg = RegistroAuditoria.objects.create(instituicao=self.a, acao="X", entidade="Y")
        reg.acao = "Z"
        with self.assertRaises(PermissionError):
            reg.save()
        with self.assertRaises(PermissionError):
            reg.delete()
        ev = seguranca.evento("LOGIN_FALHA")
        with self.assertRaises(PermissionError):
            ev.delete()

    @override_settings(SESSION_COOKIE_HTTPONLY=True)
    def test_cookies_e_cabecalhos(self):
        r = Client().get(reverse("accounts:login"))
        self.assertTrue(r.cookies["csrftoken"]["samesite"] in ("Lax", "Strict"))
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("Permissions-Policy", r)
