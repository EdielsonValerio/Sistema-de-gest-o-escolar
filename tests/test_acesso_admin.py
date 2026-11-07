"""Primeiro acesso (senha provisória), troca obrigatória e separação Superadmin x Admin da escola."""
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import PerfilUsuario
from apps.audit.models import RegistroAuditoria
from apps.core.models import Instituicao
from apps.core.services_escolas import criar_administrador, redefinir_senha

from .helpers import criar_aluno, criar_instituicao, criar_usuario

PROVISORIA = "Provisoria#2026"
NOVA = "MinhaSenhaForte#9071"
HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def entrar(client, escola, login, senha):
    return client.post(reverse("accounts:login"), {"escola": escola.pk, "usuario": login, "senha": senha})


@override_settings(PASSWORD_HASHERS=HASH)
class Base(TestCase):
    def setUp(self):
        self.a = criar_instituicao("Escola A")
        self.b = criar_instituicao("Escola B")
        self.sistema = User.objects.create_superuser("root", "r@x.com", "senha-root-123")
        self.admin_a = criar_administrador(self.a, login="diretor", nome="Dora", email="dora@x.com",
                                           senha=PROVISORIA, criado_por=self.sistema)

    def cliente_sistema(self):
        c = Client()
        c.force_login(self.sistema)
        s = c.session
        s["escola_id"] = 0
        s.save()
        return c


class PrimeiroAcessoTests(Base):
    def test_administrador_novo_nasce_com_senha_provisoria(self):
        self.assertTrue(self.admin_a.perfil.deve_trocar_senha)
        self.assertIsNone(self.admin_a.perfil.senha_trocada_em)

    def test_fluxo_completo_de_primeiro_acesso(self):
        c = Client()
        r = entrar(c, self.a, "diretor", PROVISORIA)
        self.assertEqual(r.status_code, 302)
        # qualquer tela do sistema leva à troca de senha
        for nome in ("core:dashboard", "students:aluno_lista", "accounts:usuario_lista", "finance:caixa"):
            self.assertRedirects(c.get(reverse(nome)), reverse("accounts:senha_trocar"), fetch_redirect_response=False,
                                 msg_prefix=nome)
        pagina = c.get(reverse("accounts:senha_trocar"))
        self.assertContains(pagina, "Crie a sua senha")
        self.assertNotContains(pagina, reverse("students:aluno_lista"))  # menu oculto
        # POST em outra tela também é barrado
        self.assertEqual(c.post(reverse("students:aluno_novo"), {}).status_code, 302)

        url = reverse("accounts:senha_trocar")
        base = {"senha_atual": PROVISORIA, "senha1": NOVA, "senha2": NOVA}
        for ruim, trecho in [({**base, "senha_atual": "errada"}, "não confere"),
                             ({**base, "senha2": "outra"}, "não conferem"),
                             ({**base, "senha1": PROVISORIA, "senha2": PROVISORIA}, "diferente da atual"),
                             ({**base, "senha1": "12345678", "senha2": "12345678"}, "numérica")]:
            r = c.post(url, ruim)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, trecho)
        self.admin_a.perfil.refresh_from_db()
        self.assertTrue(self.admin_a.perfil.deve_trocar_senha)

        r = c.post(url, base)
        self.assertRedirects(r, reverse("core:dashboard"), fetch_redirect_response=False)
        perfil = PerfilUsuario.objects.get(pk=self.admin_a.perfil.pk)
        self.assertFalse(perfil.deve_trocar_senha)
        self.assertIsNotNone(perfil.senha_trocada_em)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 200)  # sessão mantida, acesso normal
        self.assertEqual(c.get(reverse("students:aluno_lista")).status_code, 200)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="SENHA_INICIAL_SUBSTITUIDA", instituicao=self.a).exists())
        # a senha provisória deixou de valer; a nova vale
        outro = Client()
        self.assertEqual(entrar(outro, self.a, "diretor", PROVISORIA).status_code, 200)
        self.assertEqual(entrar(outro, self.a, "diretor", NOVA).status_code, 302)
        self.assertEqual(outro.get(reverse("core:dashboard")).status_code, 200)

    def test_pode_sair_sem_trocar(self):
        c = Client()
        entrar(c, self.a, "diretor", PROVISORIA)
        self.assertEqual(c.post(reverse("accounts:logout")).status_code, 302)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 302)
        self.assertIn("entrar", c.get(reverse("core:dashboard"))["Location"])

    def test_administrador_antigo_nao_e_afetado(self):
        legado = criar_usuario(self.a, "Administrador", username=f"{self.a.pk}.legado")
        legado.perfil.login = "legado"
        legado.perfil.save()
        self.assertFalse(legado.perfil.deve_trocar_senha)  # padrão da migração: False
        c = Client()
        self.assertEqual(entrar(c, self.a, "legado", "senha-de-teste-123").status_code, 302)
        self.assertEqual(c.get(reverse("core:dashboard")).status_code, 200)

    def test_redefinir_senha_exige_nova_troca(self):
        self.admin_a.perfil.deve_trocar_senha = False
        self.admin_a.perfil.save()
        redefinir_senha(self.admin_a, "Outra#Provisoria55", self.sistema)
        self.admin_a.perfil.refresh_from_db()
        self.assertTrue(self.admin_a.perfil.deve_trocar_senha)

    def test_troca_voluntaria_pela_propria_pessoa(self):
        u = criar_usuario(self.a, "Administrador")
        c = Client()
        c.force_login(u)
        r = c.post(reverse("accounts:senha_trocar"),
                   {"senha_atual": "senha-de-teste-123", "senha1": NOVA, "senha2": NOVA})
        self.assertEqual(r.status_code, 302)
        u.refresh_from_db()
        self.assertTrue(u.check_password(NOVA))
        self.assertTrue(RegistroAuditoria.objects.filter(acao="SENHA_ALTERADA_PELO_USUARIO").exists())
        self.assertFalse(RegistroAuditoria.objects.filter(acao="SENHA_INICIAL_SUBSTITUIDA").exists())

    def test_tela_de_troca_exige_login(self):
        self.assertEqual(Client().get(reverse("accounts:senha_trocar")).status_code, 302)

    def test_senha_nao_vai_para_auditoria(self):
        for reg in RegistroAuditoria.objects.all():
            self.assertNotIn(PROVISORIA, str(reg.dados) + reg.descricao)

    def test_usuario_criado_pelo_admin_da_escola_tem_senha_provisoria(self):
        adm = criar_usuario(self.a, "Administrador")
        c = Client()
        c.force_login(adm)
        r = c.post(reverse("accounts:usuario_novo"), {
            "username": "secre", "first_name": "Sônia", "last_name": "", "email": "", "perfil": Group.objects.get(name="Secretaria").pk,
            "is_active": "on", "senha1": PROVISORIA, "senha2": PROVISORIA})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.assertTrue(PerfilUsuario.objects.get(login="secre", instituicao=self.a).deve_trocar_senha)
        # o administrador editando a própria senha não fica bloqueado
        r = c.post(reverse("accounts:usuario_editar", args=[adm.pk]), {
            "username": adm.perfil.login, "first_name": "x", "last_name": "", "email": "",
            "perfil": Group.objects.get(name="Administrador").pk, "is_active": "on", "senha1": NOVA, "senha2": NOVA})
        self.assertEqual(r.status_code, 302)
        adm.perfil.refresh_from_db()
        self.assertFalse(adm.perfil.deve_trocar_senha)


@override_settings(PASSWORD_HASHERS=HASH, SITE_URL="https://escola.exemplo.com.br")
class EmailTests(Base):
    def dados_admin(self, **extra):
        return {"nome": "Ana", "login": "ana", "email": "ana@x.com", "senha1": PROVISORIA, "senha2": PROVISORIA,
                "enviar_email": "on", **extra}

    def test_cadastro_do_administrador_envia_acesso_por_email(self):
        c = self.cliente_sistema()
        r = c.post(reverse("core:escola_admin_novo", args=[self.a.pk]), self.dados_admin())
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ["ana@x.com"])
        for trecho in ("https://escola.exemplo.com.br/entrar/", "Escola A", "ana", PROVISORIA, "primeiro acesso"):
            self.assertIn(trecho, m.body)
        self.assertFalse(any(PROVISORIA in str(r.dados) for r in RegistroAuditoria.objects.all()))

    def test_sem_marcar_nao_envia(self):
        c = self.cliente_sistema()
        d = self.dados_admin()
        d.pop("enviar_email")
        c.post(reverse("core:escola_admin_novo", args=[self.a.pk]), d)
        self.assertEqual(len(mail.outbox), 0)

    def test_enviar_sem_email_e_recusado(self):
        c = self.cliente_sistema()
        r = c.post(reverse("core:escola_admin_novo", args=[self.a.pk]), self.dados_admin(email=""))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Informe o e-mail")
        self.assertFalse(PerfilUsuario.objects.filter(login="ana").exists())

    def test_falha_no_envio_nao_impede_o_cadastro(self):
        c = self.cliente_sistema()
        with mock.patch("apps.accounts.emails.send_mail", side_effect=OSError("smtp fora")):
            r = c.post(reverse("core:escola_admin_novo", args=[self.a.pk]), self.dados_admin(), follow=True)
        self.assertTrue(PerfilUsuario.objects.filter(login="ana", instituicao=self.a).exists())
        self.assertContains(r, "não pôde ser enviado")

    def test_redefinicao_pelo_sistema_envia_email(self):
        c = self.cliente_sistema()
        r = c.post(reverse("core:escola_admin_senha", args=[self.a.pk, self.admin_a.pk]),
                   {"senha1": "Nova#Prov2026x", "senha2": "Nova#Prov2026x", "enviar_email": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("redefinida", mail.outbox[0].body)

    def test_cadastro_de_escola_com_envio(self):
        c = self.cliente_sistema()
        r = c.post(reverse("core:escola_nova"), {
            "nome": "Colégio Novo", "ativa": "on", "admin-nome": "Rui", "admin-login": "rui", "admin-email": "rui@x.com",
            "admin-senha1": PROVISORIA, "admin-senha2": PROVISORIA, "admin-enviar_email": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form_admin"].errors)
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(PerfilUsuario.objects.get(login="rui").deve_trocar_senha)


@override_settings(PASSWORD_HASHERS=HASH)
class SeparacaoDeAdministradoresTests(Base):
    def setUp(self):
        super().setUp()
        self.admin_a.perfil.deve_trocar_senha = False
        self.admin_a.perfil.save()
        self.usuario_a = criar_usuario(self.a, "Secretaria")
        self.aluno_a = criar_aluno(self.a, self.usuario_a, nome="Aluno da Escola A")
        self.usuario_b = criar_usuario(self.b, "Secretaria")
        self.aluno_b = criar_aluno(self.b, self.usuario_b, nome="Aluno da Escola B")

    def cliente_admin(self, user=None):
        c = Client()
        c.force_login(user or self.admin_a)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        return c

    def test_admin_da_escola_nao_acessa_area_do_sistema(self):
        c = self.cliente_admin()
        for nome, args in [("escola_lista", []), ("escola_nova", []), ("escola_editar", [self.b.pk]),
                           ("escola_admin_novo", [self.b.pk]), ("escola_admin_senha", [self.b.pk, self.admin_a.pk])]:
            self.assertEqual(c.get(reverse(f"core:{nome}", args=args)).status_code, 403, nome)
        self.assertEqual(c.post(reverse("core:escola_acessar", args=[self.b.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("core:escola_sair")).status_code, 403)

    def test_admin_da_escola_so_ve_a_propria_escola(self):
        c = self.cliente_admin()
        pagina = c.get(reverse("students:aluno_lista"))
        self.assertContains(pagina, "Aluno da Escola A")
        self.assertNotContains(pagina, "Aluno da Escola B")
        self.assertEqual(c.get(reverse("students:aluno_ficha", args=[self.aluno_b.pk])).status_code, 404)

    def test_adulterar_a_sessao_nao_abre_outra_escola(self):
        c = self.cliente_admin()
        s = c.session
        s["suporte_escola_id"] = self.b.pk
        s["escola_id"] = self.b.pk
        s.save()
        r = c.get(reverse("students:aluno_lista"), follow=True)  # sessão inconsistente é encerrada
        self.assertNotContains(r, "Aluno da Escola B")
        c2 = self.cliente_admin()
        s = c2.session
        s["suporte_escola_id"] = self.b.pk
        s.save()
        self.assertNotContains(c2.get(reverse("students:aluno_lista")), "Aluno da Escola B")

    def test_superadmin_sem_escola_aberta_nao_ve_dados(self):
        c = self.cliente_sistema()
        self.assertEqual(c.get(reverse("students:aluno_lista")).status_code, 403)
        self.assertRedirects(c.get(reverse("core:dashboard")), reverse("core:escola_lista"), fetch_redirect_response=False)

    def test_superadmin_acessa_qualquer_escola_em_modo_suporte(self):
        c = self.cliente_sistema()
        r = c.post(reverse("core:escola_acessar", args=[self.b.pk]))
        self.assertRedirects(r, reverse("core:dashboard"), fetch_redirect_response=False)
        pagina = c.get(reverse("students:aluno_lista"))
        self.assertContains(pagina, "Aluno da Escola B")
        self.assertNotContains(pagina, "Aluno da Escola A")
        self.assertContains(pagina, "Modo suporte")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="SUPORTE_ACESSO_ESCOLA", instituicao=self.b).exists())
        # troca para a escola A sem sair
        c.post(reverse("core:escola_acessar", args=[self.a.pk]))
        self.assertContains(c.get(reverse("students:aluno_lista")), "Aluno da Escola A")
        # gerencia usuários da escola aberta, mas só dela
        lista = c.get(reverse("accounts:usuario_lista"))
        self.assertContains(lista, "diretor")
        self.assertEqual(c.get(reverse("accounts:usuario_editar", args=[self.usuario_b.pk])).status_code, 404)
        # sai do modo suporte
        c.post(reverse("core:escola_sair"))
        self.assertEqual(c.get(reverse("students:aluno_lista")).status_code, 403)

    def test_acoes_em_modo_suporte_ficam_na_escola_certa(self):
        c = self.cliente_sistema()
        c.post(reverse("core:escola_acessar", args=[self.b.pk]))
        r = c.post(reverse("accounts:usuario_novo"), {
            "username": "novo", "first_name": "N", "last_name": "", "email": "",
            "perfil": Group.objects.get(name="Secretaria").pk, "is_active": "on", "senha1": PROVISORIA, "senha2": PROVISORIA})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(PerfilUsuario.objects.filter(login="novo", instituicao=self.b).exists())
        self.assertFalse(PerfilUsuario.objects.filter(login="novo", instituicao=self.a).exists())
        reg = RegistroAuditoria.objects.get(acao="USUARIO_CRIADO")
        self.assertEqual((reg.instituicao_id, reg.usuario_id), (self.b.pk, self.sistema.pk))

    def test_modo_suporte_nao_vale_para_escola_inexistente(self):
        c = self.cliente_sistema()
        self.assertEqual(c.post(reverse("core:escola_acessar", args=[99999])).status_code, 404)
        s = c.session
        s["suporte_escola_id"] = 99999
        s.save()
        self.assertEqual(c.get(reverse("students:aluno_lista")).status_code, 403)

    def test_superadmin_gerencia_administradores_de_qualquer_escola(self):
        c = self.cliente_sistema()
        self.assertContains(c.get(reverse("core:escola_editar", args=[self.a.pk])), "diretor")
        r = c.post(reverse("core:escola_admin_novo", args=[self.b.pk]), {
            "nome": "Bia", "login": "bia", "email": "", "senha1": PROVISORIA, "senha2": PROVISORIA})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(PerfilUsuario.objects.get(login="bia", instituicao=self.b).deve_trocar_senha)

    def test_administrador_da_escola_nao_e_superusuario(self):
        self.assertFalse(self.admin_a.is_superuser or self.admin_a.is_staff)
        self.assertFalse(self.admin_a.has_perm("core.add_instituicao"))

    def test_superadmin_entra_so_pelo_modo_sistema(self):
        c = Client()
        r = c.post(reverse("accounts:login"), {"sistema": "1", "usuario": "root", "senha": "senha-root-123"})
        self.assertRedirects(r, reverse("core:escola_lista"), fetch_redirect_response=False)
        # administrador de escola não entra pelo modo sistema
        c2 = Client()
        c2.post(reverse("accounts:login"), {"sistema": "1", "usuario": self.admin_a.username, "senha": PROVISORIA})
        self.assertEqual(c2.get(reverse("core:escola_lista")).status_code, 302)
