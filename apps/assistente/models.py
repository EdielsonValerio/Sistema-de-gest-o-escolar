"""Assistente de IA (chat integrado ao Claude).

* Habilitado por escola pelo administrador do sistema (`habilitada`), conforme a assinatura contratada.
* Cada escola usa **a própria chave de API** (cifrada) e paga direto ao fornecedor.
* O chat só enxerga **dados agregados** (LGPD): veja `ferramentas.py`.
"""
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.billing import crypto

MODELOS = [
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 (equilibrado — recomendado)"),
    ("claude-haiku-5-5", "Claude Haiku 5.5 (mais rápido e econômico)"),
    ("claude-opus-5-5", "Claude Opus 5.5 (mais capaz, maior custo)"),
]
LIMITE_PADRAO = 500_000


class ConfiguracaoIA(models.Model):
    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE, related_name="configuracao_ia")
    habilitada = models.BooleanField("habilitada pelo sistema (assinatura contratada)", default=False)
    api_key_cifrada = models.TextField(blank=True, editable=False)
    modelo = models.CharField("modelo", max_length=40, choices=MODELOS, default=MODELOS[0][0])
    limite_mensal_tokens = models.PositiveIntegerField("limite mensal de tokens (0 = sem limite)", default=LIMITE_PADRAO)
    termo_aceito_em = models.DateTimeField(null=True, blank=True, editable=False)
    termo_aceito_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                         related_name="+", editable=False)
    testada_em = models.DateTimeField(null=True, blank=True, editable=False)
    ativa = models.BooleanField("ativa na escola", default=False)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração do assistente de IA"
        verbose_name_plural = "configurações do assistente de IA"
        permissions = [("configurar_ia", "Pode configurar o assistente de IA")]

    def __str__(self):
        return f"Assistente de IA — {self.instituicao}"

    @property
    def api_key(self):
        return crypto.decifrar(self.api_key_cifrada)

    def definir_api_key(self, valor):
        self.api_key_cifrada = crypto.cifrar(valor.strip())

    @property
    def tem_api_key(self):
        return bool(self.api_key_cifrada)

    @property
    def pronta(self):
        """Pode ser usada no chat: contratada, com chave, testada e ligada pela escola."""
        return self.habilitada and self.ativa and self.tem_api_key and self.termo_aceito_em is not None


class Conversa(models.Model):
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="conversas_ia")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversas_ia")
    titulo = models.CharField(max_length=120, default="Nova conversa")
    criada_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizada_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-atualizada_em", "-id"]
        permissions = [("usar_assistente", "Pode usar o assistente de IA")]
        indexes = [models.Index(fields=["usuario", "-atualizada_em"])]


class Mensagem(models.Model):
    class Papel(models.TextChoices):
        USUARIO = "USUARIO", "Usuário"
        ASSISTENTE = "ASSISTENTE", "Assistente"

    INSTITUICAO_DE = "conversa"
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+", editable=False)
    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="mensagens")
    papel = models.CharField(max_length=10, choices=Papel.choices)
    texto = models.TextField()
    grafico = models.JSONField(null=True, blank=True)
    ferramentas = models.JSONField(default=list, blank=True)
    aviso = models.CharField(max_length=200, blank=True)
    tokens_entrada = models.PositiveIntegerField(default=0)
    tokens_saida = models.PositiveIntegerField(default=0)
    criada_em = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["criada_em", "id"]


class UsoMensal(models.Model):
    """Consumo agregado do mês (a escola paga ao fornecedor; isto só ajuda a controlar e a limitar)."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    mes = models.DateField()
    perguntas = models.PositiveIntegerField(default=0)
    tokens_entrada = models.PositiveBigIntegerField(default=0)
    tokens_saida = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["instituicao", "mes"], name="uniq_uso_ia_mes")]

    @property
    def tokens(self):
        return self.tokens_entrada + self.tokens_saida
