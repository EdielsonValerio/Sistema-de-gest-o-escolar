"""Lacunas de segurança fechadas na auditoria: eventos de webhook e de acesso negado, retenção, rotação de chaves,
trava de produção, open redirect, exportação LGPD e tela de eventos."""
import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import MfaDispositivo, TentativaLogin, TokenConta
from apps.assistente.models import Conversa
from apps.audit.models import EventoSeguranca, RegistroAuditoria
from apps.billing import crypto
from apps.billing.models import ConfiguracaoBancaria
from apps.billing.views import _destino_seguro

from .helpers import criar_aluno, criar_instituicao, criar_usuario
from .test_cobranca_bancaria import Base as BaseCobranca, WebhookTests as _WebhookBase
from .test_diario import HASH

RAIZ = Path(__file__).resolve().parent.parent


class WebhookEventosTests(_WebhookBase):
    def test_falha_de_token_vira_evento_e_bloqueia_apos_repeticoes(self):
        for _ in range(10):
            self.assertEqual(self.enviar(self.evento(), token="errado").status_code, 401)
        self.assertEqual(EventoSeguranca.objects.filter(tipo="WEBHOOK_FALHA", instituicao=self.inst).count(), 10)
        # mesmo com o token certo, o IP abusivo fica bloqueado temporariamente (429)
        self.assertEqual(self.enviar(self.evento()).status_code, 429)
        self.assertNotIn(self.token, " ".join(EventoSeguranca.objects.values_list("detalhe", flat=True)))

    def test_sucesso_nao_gera_evento_de_falha(self):
        with self.asaas_pago():
            self.assertEqual(self.enviar(self.evento("evt_ok")).status_code, 200)
        self.assertFalse(EventoSeguranca.objects.filter(tipo="WEBHOOK_FALHA").exists())

    def test_replay_do_mesmo_evento_nao_baixa_duas_vezes(self):
        from apps.finance.models import Recebimento
        with self.asaas_pago():
            self.enviar(self.evento("evt_r"))
            n = Recebimento.objects.count()
            r = self.enviar(self.evento("evt_r"))
        self.assertEqual(json.loads(r.content)["resultado"], "duplicado")
        self.assertEqual(Recebimento.objects.count(), n)


@override_settings(PASSWORD_HASHERS=HASH)
class AcessoNegadoTests(TestCase):
    def setUp(self):
        self.a = criar_instituicao("Escola A")
        self.prof = criar_usuario(self.a, "Professor")
        self.adm = criar_usuario(self.a, "Administrador")
        self.sec = criar_usuario(self.a, "Secretaria")

    def cli(self, u):
        from django.test import Client
        c = Client()
        c.force_login(u)
        s = c.session
        s["escola_id"] = self.a.pk
        s.save()
        return c

    def test_403_vira_evento_sem_parametros_e_com_teto(self):
        c = self.cli(self.prof)
        for _ in range(30):
            self.assertEqual(c.get("/alunos/novo/?cpf=12345678909").status_code, 403)
        ev = EventoSeguranca.objects.filter(tipo="ACESSO_NEGADO", usuario=self.prof)
        self.assertEqual(ev.count(), 20)  # teto por usuário/hora
        d = ev.first().detalhe
        self.assertIn("/alunos/novo/", d)
        self.assertNotIn("12345678909", d)  # query string nunca é registrada

    def test_tela_de_eventos_por_perfil(self):
        EventoSeguranca.objects.create(tipo="LOGIN_BLOQUEADO", instituicao=self.a, detalhe="x")
        outra = criar_instituicao("Escola B")
        EventoSeguranca.objects.create(tipo="LOGIN_BLOQUEADO", instituicao=outra, detalhe="da-outra-escola")
        r = self.cli(self.adm).get(reverse("audit:eventos"))
        self.assertContains(r, "Login bloqueado")
        self.assertNotContains(r, "da-outra-escola")
        self.assertEqual(self.cli(self.sec).get(reverse("audit:eventos")).status_code, 403)
        su = User.objects.create_superuser("root", "r@x.com", "senha-de-teste-123")
        from django.test import Client
        c = Client()
        c.force_login(su)
        self.assertContains(c.get(reverse("audit:eventos")), "da-outra-escola")


class OpenRedirectTests(TestCase):
    def test_destinos_externos_e_disfarcados_sao_recusados(self):
        req = RequestFactory().get("/", HTTP_HOST="testserver")
        for ruim in ("//evil.com", "/\\evil.com", "https://evil.com", "javascript:alert(1)", "", "evil.com", "/\t/evil.com"):
            self.assertFalse(_destino_seguro(req, ruim), ruim)
        self.assertTrue(_destino_seguro(req, "/financeiro/parcelas/10/"))


@override_settings(PASSWORD_HASHERS=HASH)
class RetencaoTests(TestCase):
    def test_limpa_dados_vencidos_e_preserva_auditoria(self):
        inst = criar_instituicao()
        u = criar_usuario(inst, "Administrador")
        velho = timezone.now() - timedelta(days=500)
        t = TentativaLogin.objects.create(tipo="LOGIN_IP", chave="a" * 64)
        TentativaLogin.objects.filter(pk=t.pk).update(criado_em=velho)
        TentativaLogin.objects.create(tipo="LOGIN_IP", chave="b" * 64)  # recente: fica
        TokenConta.objects.create(user=u, finalidade="ATIVACAO", token_hash="h" * 64, expira_em=velho)
        c = Conversa.objects.create(instituicao=inst, usuario=u)
        Conversa.objects.filter(pk=c.pk).update(atualizada_em=velho)
        EventoSeguranca.objects.create(tipo="LOGIN_FALHA")
        EventoSeguranca.objects.filter().update(criado_em=velho)
        RegistroAuditoria.objects.create(instituicao=inst, acao="X", entidade="Y")
        call_command("limpar_seguranca", "--simular", stdout=StringIO())
        self.assertEqual(TentativaLogin.objects.count(), 2)
        call_command("limpar_seguranca", stdout=StringIO())
        self.assertEqual(TentativaLogin.objects.count(), 1)
        self.assertFalse(TokenConta.objects.exists())
        self.assertFalse(Conversa.objects.exists())
        self.assertFalse(EventoSeguranca.objects.exists())
        self.assertEqual(RegistroAuditoria.objects.count(), 1)  # trilha de auditoria nunca é apagada


@override_settings(PASSWORD_HASHERS=HASH)
class RotacaoDeChavesTests(BaseCobranca):
    def test_rotacao_mantem_segredos_legiveis_com_a_chave_nova(self):
        antiga, nova = Fernet.generate_key().decode(), Fernet.generate_key().decode()
        with override_env(BILLING_CRYPTO_KEY=antiga):
            from apps.billing import services
            services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "ambiente": "SANDBOX", "api_key": "$aact_hmlg_seg"}, self.adm)
        with override_env(BILLING_CRYPTO_KEY=f"{nova},{antiga}"):
            call_command("rotacionar_segredos", stdout=StringIO())
        cfg = ConfiguracaoBancaria.objects.get(instituicao=self.inst)
        with override_env(BILLING_CRYPTO_KEY=nova):  # a antiga já pode ser removida
            self.assertEqual(cfg.api_key, "$aact_hmlg_seg")
        with override_env(BILLING_CRYPTO_KEY=antiga), self.assertRaises(crypto.SegredoIlegivel):
            cfg.api_key


class override_env:
    def __init__(self, **kw): self.kw = kw
    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.kw}
        os.environ.update(self.kw)
    def __exit__(self, *a):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


class TravaDeProducaoTests(TestCase):
    def _check(self, **env):
        base = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
        base.update(env, DJANGO_DEBUG="False")
        return subprocess.run([sys.executable, "manage.py", "check"], cwd=RAIZ, env=base, capture_output=True, text=True)

    def test_producao_recusa_configuracao_fraca(self):
        r = self._check(DJANGO_SECRET_KEY="curta", DJANGO_ALLOWED_HOSTS="a.com")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", r.stderr)
        r = self._check(DJANGO_SECRET_KEY="k" * 60 + "x1")  # sem ALLOWED_HOSTS
        self.assertIn("DJANGO_ALLOWED_HOSTS", r.stderr)
        r = self._check(DJANGO_SECRET_KEY="Zq9" * 20, DJANGO_ALLOWED_HOSTS="*")
        self.assertIn("DJANGO_ALLOWED_HOSTS", r.stderr)

    def test_producao_aceita_configuracao_correta(self):
        r = self._check(DJANGO_SECRET_KEY="Zq9" * 20, DJANGO_ALLOWED_HOSTS="escola.exemplo.com")
        self.assertEqual(r.returncode, 0, r.stderr)


@override_settings(PASSWORD_HASHERS=HASH)
class ExportacaoLgpdTests(TestCase):
    def setUp(self):
        self.a, self.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        self.adm, self.sec = criar_usuario(self.a, "Administrador"), criar_usuario(self.a, "Secretaria")
        self.adm_b = criar_usuario(self.b, "Administrador")
        self.aluno = criar_aluno(self.a, self.sec, nome="Titular Fictício", cpf="529.982.247-25")

    def cli(self, u, escola):
        from django.test import Client
        c = Client()
        c.force_login(u)
        s = c.session
        s["escola_id"] = escola.pk
        s.save()
        return c

    def test_exporta_json_auditado_e_restrito(self):
        url = reverse("students:aluno_exportar_dados", args=[self.aluno.pk])
        r = self.cli(self.adm, self.a).get(url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r["Content-Disposition"])
        self.assertEqual(r["Cache-Control"], "no-store")
        dados = json.loads(r.content)
        self.assertEqual(dados["aluno"]["nome"], "Titular Fictício")
        self.assertNotIn("password", r.content.decode().lower())
        self.assertTrue(RegistroAuditoria.objects.filter(acao="DADOS_TITULAR_EXPORTADOS", aluno_id=self.aluno.pk).exists())
        self.assertEqual(self.cli(self.sec, self.a).get(url).status_code, 403)   # sem a permissão
        self.assertEqual(self.cli(self.adm_b, self.b).get(url).status_code, 404)  # outra escola


class AlertasTests(TestCase):
    def test_alerta_so_acima_do_limite_e_envia_email_ao_administrador_do_sistema(self):
        inst = criar_instituicao("Escola Alerta")
        User.objects.create_superuser("root", "root@exemplo.com", "senha-de-teste-123")
        saida = StringIO()
        call_command("alertas_seguranca", stdout=saida)
        self.assertIn("Sem alertas", saida.getvalue())
        for _ in range(3):
            EventoSeguranca.objects.create(tipo="LOGIN_BLOQUEADO", instituicao=inst)
        EventoSeguranca.objects.create(tipo="LOGIN_FALHA", instituicao=inst)  # abaixo do limite: não alerta
        from django.core import mail
        with override_settings(EMAIL_CONFIGURADO=True, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            call_command("alertas_seguranca", stdout=saida)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("LOGIN_BLOQUEADO", mail.outbox[0].body)
        self.assertNotIn("LOGIN_FALHA", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["root@exemplo.com"])


class AnonimizacaoDeNomesTests(TestCase):
    def setUp(self):
        from apps.assistente import lgpd
        self.lgpd = lgpd
        self.a, self.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        self.u = criar_usuario(self.a, "Secretaria")
        criar_aluno(self.a, self.u, nome="Ana Beatriz Souza", nome_mae="Maria José Oliveira")
        criar_aluno(self.b, criar_usuario(self.b, "Secretaria"), nome="Carlos Eduardo Lima")

    def test_mascara_nome_completo_parcial_acento_e_caixa(self):
        nomes = self.lgpd.nomes_da_escola(self.a)
        for texto in ("notas da Ana Beatriz Souza", "notas de ANA BEATRIZ", "mãe: maria jose oliveira, ok",
                      "Maria  José   Oliveira"):
            limpo, removeu = self.lgpd.redigir(texto, nomes)
            self.assertTrue(removeu, texto)
            self.assertIn("[nome removido]", limpo)
            for pedaco in ("Beatriz", "BEATRIZ", "Oliveira", "oliveira"):
                self.assertNotIn(pedaco, limpo, texto)

    def test_nao_mascara_palavras_comuns_nem_nomes_de_outra_escola(self):
        nomes = self.lgpd.nomes_da_escola(self.a)
        limpo, removeu = self.lgpd.redigir("Quantas turmas e quantos alunos? Carlos Eduardo Lima", nomes)
        self.assertEqual(limpo, "Quantas turmas e quantos alunos? Carlos Eduardo Lima")  # nome da Escola B não é da A
        self.assertFalse(removeu)
        self.assertNotIn("Carlos Eduardo Lima", self.lgpd.nomes_da_escola(self.a))

    def test_remove_tambem_cpf_e_email_e_sem_nomes_funciona_como_antes(self):
        limpo, _ = self.lgpd.redigir("cpf 123.456.789-09 e a@b.com")
        self.assertNotIn("123.456", limpo)
        self.assertNotIn("a@b.com", limpo)


@override_settings(LIMITE_TAXA_ATIVO=True, LIMITE_REQ_USUARIO_MIN=5, LIMITE_POST_USUARIO_MIN=3,
                   LIMITE_REQ_ANONIMO_MIN=4, PASSWORD_HASHERS=HASH)
class LimiteTaxaTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.a = criar_instituicao("Escola A")
        self.u = criar_usuario(self.a, "Secretaria")
        self.c = self.cliente(self.u)

    def cliente(self, user):
        from django.test import Client
        c = Client()
        c.force_login(user)
        s = c.session
        s["escola_id"] = user.perfil.instituicao.pk
        s.save()
        return c

    def test_usuario_autenticado_excedendo_recebe_429_com_retry_after_e_evento(self):
        url = reverse("students:aluno_lista")
        codigos = [self.c.get(url).status_code for _ in range(7)]
        self.assertEqual(codigos[:5], [200] * 5)
        self.assertEqual(codigos[5:], [429, 429])
        r = self.c.get(url)
        self.assertEqual(r.status_code, 429)
        self.assertTrue(0 < int(r["Retry-After"]) <= 60)
        self.assertEqual(EventoSeguranca.objects.filter(tipo="LIMITE_TAXA", usuario=self.u).count(), 1)  # 1 por janela

    def test_posts_tem_teto_proprio_e_outro_usuario_nao_e_afetado(self):
        url = reverse("students:aluno_lista")
        codigos = [self.c.post(url, {}).status_code for _ in range(5)]
        self.assertEqual(codigos[3:], [429, 429])
        outro = criar_usuario(self.a, "Secretaria")
        self.assertEqual(self.cliente(outro).get(url).status_code, 200)

    def test_anonimo_limitado_por_ip_e_estaticos_nao_contam(self):
        from django.test import Client
        c = Client()
        for _ in range(10):
            self.assertNotEqual(c.get("/static/css/app.css").status_code, 429)
        codigos = [c.get(reverse("accounts:login")).status_code for _ in range(6)]
        self.assertEqual(codigos[:4], [200] * 4)
        self.assertEqual(codigos[4:], [429, 429])

    def test_janela_seguinte_libera(self):
        from unittest import mock
        url = reverse("students:aluno_lista")
        for _ in range(6):
            self.c.get(url)
        self.assertEqual(self.c.get(url).status_code, 429)
        import time
        with mock.patch("time.time", return_value=time.time() + 61):
            self.assertEqual(self.c.get(url).status_code, 200)

    def test_desligado_por_configuracao(self):
        with override_settings(LIMITE_TAXA_ATIVO=False):
            for _ in range(10):
                self.assertEqual(self.c.get(reverse("students:aluno_lista")).status_code, 200)
