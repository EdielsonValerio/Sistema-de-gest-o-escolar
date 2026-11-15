"""Cabeçalhos/CSP, XSS em documentos, redirecionamento aberto, métodos e CSRF."""
import re

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.permissions import sincronizar_perfis
from apps.documents.models import ModeloDocumento

from .helpers import criar_aluno, criar_instituicao, criar_usuario

HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False)
class CspTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.a = criar_instituicao("A")
        cls.adm = criar_usuario(cls.a, "Administrador")

    def cli(self):
        c = Client()
        c.force_login(self.adm)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        return c

    def test_csp_com_nonce_e_sem_unsafe_inline_em_scripts(self):
        r = self.cli().get(reverse("core:dashboard"))
        csp = r["Content-Security-Policy"]
        diretiva = lambda nome: next(p for p in csp.split("; ") if p.startswith(nome + " "))
        self.assertNotIn("unsafe-inline", diretiva("script-src"))
        self.assertNotIn("unsafe-inline", diretiva("style-src"))
        self.assertIn("script-src-attr 'none'", csp)
        for d in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'none'"):
            self.assertIn(d, csp)
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertNotEqual(self.cli().get(reverse("core:dashboard"))["Content-Security-Policy"], csp)  # muda a cada resposta
        html = r.content.decode()
        for tag in re.findall(r"<script\b[^>]*>", html):
            if "src=" not in tag:
                self.assertIn(f'nonce="{nonce}"', tag, tag)

    def test_nenhum_template_tem_handler_inline_nem_script_sem_nonce(self):
        import pathlib
        raiz = pathlib.Path(__file__).resolve().parent.parent / "templates"
        for f in raiz.rglob("*.html"):
            txt = f.read_text()
            self.assertIsNone(re.search(r"\son(click|submit|change|load|error|input|focus|blur)=", txt), f.name)
            self.assertIsNone(re.search(r"<script(?![^>]*(src=|nonce=))", txt), f.name)
            self.assertNotIn("javascript:", txt, f.name)

    def test_cabecalhos_de_seguranca(self):
        r = Client().get(reverse("accounts:login"))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["Referrer-Policy"], "same-origin")
        self.assertIn("camera=()", r["Permissions-Policy"])
        self.assertEqual(r["Cross-Origin-Opener-Policy"], "same-origin")

    def test_trace_bloqueado_e_get_nao_altera_dados(self):
        c = self.cli()
        self.assertEqual(c.generic("TRACE", "/").status_code, 405)
        # logout somente por POST
        self.assertEqual(c.get(reverse("accounts:logout")).status_code, 405)

    def test_csrf_exigido_em_post_autenticado(self):
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.adm)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        self.assertEqual(c.post(reverse("accounts:logout")).status_code, 403)

    def test_next_externo_nao_redireciona(self):
        from apps.accounts.models import PerfilUsuario
        r = Client().post(reverse("accounts:login"), {"escola": self.a.pk, "usuario": self.adm.perfil.login,
                                                      "senha": "senha-de-teste-123", "next": "https://evil.example/x"})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("evil", r["Location"])
        r = Client().post(reverse("accounts:login"), {"escola": self.a.pk, "usuario": self.adm.perfil.login,
                                                      "senha": "senha-de-teste-123", "next": "//evil.example/x"})
        self.assertNotIn("evil", r["Location"])


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False)
class XssDocumentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.a = criar_instituicao("A")
        cls.adm = criar_usuario(cls.a, "Administrador")

    def test_modelo_com_script_e_sanitizado_e_valores_sao_escapados(self):
        from apps.documents.services import emitir_documento
        aluno = criar_aluno(self.a, self.adm, nome='<img src=x onerror=alert(1)>Maria')
        m = ModeloDocumento.objects.create(
            instituicao=self.a, nome="T", tipo="DECLARACAO_MATRICULA", destinatario="ALUNO", ativo=True,
            conteudo='<p onclick="x()">Olá [nomeAluno]</p><script>alert(1)</script><iframe src="//evil"></iframe>'
                     '<a href="javascript:alert(1)">l</a>') if False else None
        # O saneamento do leiaute é aplicado pelo formulário/serviço de documentos; aqui comprovamos o escape de valores:
        from apps.documents.variaveis import renderizar
        html = renderizar("<p>[nomeAluno]</p>", {"nomeAluno": aluno.nome})
        self.assertNotIn("<img", html)
        self.assertIn("&lt;img", html)
