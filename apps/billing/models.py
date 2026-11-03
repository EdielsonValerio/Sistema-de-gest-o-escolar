"""Cobrança bancária: configuração do banco/provedor de cada escola e os boletos (com Pix) registrados.

* `ConfiguracaoBancaria`: uma por escola. Guarda o provedor, o ambiente e os segredos **criptografados**.
* `Cobranca`: boleto de uma parcela (ou boleto de TESTE, sem parcela). Nada é apagado: cancela-se.
* `EventoBanco`: avisos (webhooks) recebidos do banco, para não processar o mesmo aviso duas vezes.
* `TituloSimulado`: "banco de mentira" usado só no modo simulado (treinamento e testes automatizados).
"""
import hashlib
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from . import crypto


def _dinheiro(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, **kw)


class Provedor(models.TextChoices):
    SIMULADO = "SIMULADO", "Banco simulado (treinamento)"
    ASAAS = "ASAAS", "Asaas"


class Ambiente(models.TextChoices):
    SANDBOX = "SANDBOX", "Teste (sandbox)"
    PRODUCAO = "PRODUCAO", "Produção"


class ConfiguracaoBancaria(models.Model):
    class Status(models.TextChoices):
        CONFIGURANDO = "CONFIGURANDO", "Em configuração"
        ATIVA = "ATIVA", "Ativa"
        SUSPENSA = "SUSPENSA", "Suspensa"

    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE,
                                       related_name="configuracao_bancaria")
    provedor = models.CharField("provedor / banco", max_length=10, choices=Provedor.choices,
                                default=Provedor.SIMULADO)
    ambiente = models.CharField("ambiente", max_length=10, choices=Ambiente.choices, default=Ambiente.SANDBOX)
    api_key_cifrada = models.TextField(blank=True, editable=False)
    webhook_token_cifrado = models.TextField(blank=True, editable=False)
    webhook_slug = models.UUIDField("identificador do webhook", default=uuid.uuid4, unique=True, editable=False)

    # Dados de conferência (impressos no boleto e exibidos na tela); a conta de cobrança em si é a do provedor.
    banco_nome = models.CharField("banco da escola", max_length=80, blank=True)
    agencia = models.CharField("agência", max_length=20, blank=True)
    conta = models.CharField("conta", max_length=30, blank=True)
    beneficiario_nome = models.CharField("beneficiário (razão social)", max_length=150, blank=True)
    beneficiario_documento = models.CharField("CNPJ do beneficiário", max_length=18, blank=True)
    instrucoes = models.CharField("instruções no boleto", max_length=200, blank=True,
                                  help_text="Ex.: Não receber após 30 dias do vencimento.")

    status = models.CharField("situação", max_length=12, choices=Status.choices, default=Status.CONFIGURANDO)
    ativada_em = models.DateTimeField(null=True, blank=True, editable=False)
    ativada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+", editable=False)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração bancária"
        verbose_name_plural = "configurações bancárias"
        permissions = [("configurar_cobranca", "Pode configurar a cobrança bancária")]

    def __str__(self):
        return f"Cobrança bancária — {self.instituicao}"

    # ---- segredos (sempre cifrados em repouso)
    @property
    def api_key(self):
        return crypto.decifrar(self.api_key_cifrada)

    def definir_api_key(self, valor):
        self.api_key_cifrada = crypto.cifrar(valor.strip())

    @property
    def webhook_token(self):
        return crypto.decifrar(self.webhook_token_cifrado)

    def definir_webhook_token(self, valor):
        self.webhook_token_cifrado = crypto.cifrar(valor)

    @property
    def tem_api_key(self):
        return bool(self.api_key_cifrada)

    def assinatura(self):
        """Impressão digital de provedor + ambiente + chave. Se mudar, o teste anterior deixa de valer."""
        try:
            chave = self.api_key
        except crypto.SegredoIlegivel:
            chave = "?"
        return hashlib.sha256(f"{self.provedor}|{self.ambiente}|{chave}".encode()).hexdigest()

    @property
    def completa(self):
        return self.provedor == Provedor.SIMULADO or self.tem_api_key


class Cobranca(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Aguardando pagamento"
        PAGA = "PAGA", "Paga"
        CANCELADA = "CANCELADA", "Cancelada"
        ERRO = "ERRO", "Erro no registro"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="cobrancas")
    parcela = models.ForeignKey("finance.Parcela", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="cobrancas")
    eh_teste = models.BooleanField("boleto de teste", default=False)
    assinatura = models.CharField(max_length=64, blank=True, editable=False,
                                  help_text="Configuração (provedor/ambiente/chave) usada na emissão.")
    provedor = models.CharField(max_length=10, choices=Provedor.choices)
    ambiente = models.CharField(max_length=10, choices=Ambiente.choices)
    id_externo = models.CharField("identificador no banco", max_length=60, blank=True, db_index=True)
    nosso_numero = models.CharField("nosso número", max_length=40, blank=True)
    linha_digitavel = models.CharField("linha digitável", max_length=60, blank=True)
    codigo_barras = models.CharField("código de barras", max_length=60, blank=True)
    pix_copia_cola = models.TextField("Pix copia e cola", blank=True)
    pix_qrcode = models.TextField("QR Code Pix (PNG em base64)", blank=True)
    url_boleto = models.URLField("PDF do banco", max_length=500, blank=True)
    url_fatura = models.URLField("página do banco", max_length=500, blank=True)
    descricao = models.CharField(max_length=200, blank=True)
    pagador_nome = models.CharField("pagador", max_length=150, blank=True)
    valor = _dinheiro(verbose_name="valor", validators=[MinValueValidator(Decimal("0.01"))])
    vencimento = models.DateField("vencimento")
    status = models.CharField("situação", max_length=10, choices=Status.choices, default=Status.PENDENTE)
    erro = models.TextField("mensagem de erro", blank=True)
    pago_em = models.DateField("pago em", null=True, blank=True)
    valor_pago = _dinheiro(verbose_name="valor pago", null=True, blank=True)
    recebimento = models.ForeignKey("finance.Recebimento", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="cobrancas")
    divergencia = models.TextField("divergência a conferir", blank=True)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cobrança bancária"
        verbose_name_plural = "cobranças bancárias"
        ordering = ["-criado_em", "-id"]
        permissions = [("gerar_boleto", "Pode gerar boletos"), ("cancelar_boleto", "Pode cancelar boletos")]
        indexes = [models.Index(fields=["instituicao", "status"])]
        constraints = [
            models.UniqueConstraint(fields=["provedor", "id_externo"], condition=~Q(id_externo=""),
                                    name="uniq_cobranca_id_externo"),
            # No máximo um boleto vigente (aguardando ou pago) por parcela.
            models.UniqueConstraint(fields=["parcela"], condition=Q(parcela__isnull=False, status__in=["PENDENTE", "PAGA"]),
                                    name="uniq_cobranca_vigente_parcela"),
            models.CheckConstraint(condition=Q(valor__gt=0), name="chk_cobranca_valor_positivo"),
            models.CheckConstraint(condition=Q(eh_teste=True) | Q(parcela__isnull=False) | Q(status="CANCELADA"),
                                   name="chk_cobranca_parcela_ou_teste"),
        ]

    def __str__(self):
        return f"Boleto {self.nosso_numero or self.id_externo or self.pk}"

    @property
    def vigente(self):
        return self.status in (self.Status.PENDENTE, self.Status.PAGA)

    @property
    def qrcode_src(self):
        return f"data:image/png;base64,{self.pix_qrcode}" if self.pix_qrcode else ""


class EventoBanco(models.Model):
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    provedor = models.CharField(max_length=10, choices=Provedor.choices)
    evento_id = models.CharField(max_length=80)
    tipo = models.CharField(max_length=60)
    id_externo = models.CharField(max_length=60, blank=True)
    dados = models.JSONField(default=dict)  # só campos não pessoais (status, valor, datas)
    processado = models.BooleanField(default=False)
    resultado = models.CharField(max_length=200, blank=True)
    recebido_em = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provedor", "evento_id"], name="uniq_evento_banco")]


class TituloSimulado(models.Model):
    """Estado de um boleto no banco simulado. Não pertence a nenhuma escola: só existe para treinamento/testes."""

    id_externo = models.CharField(max_length=40, unique=True)
    valor = _dinheiro()
    vencimento = models.DateField()
    pago = models.BooleanField(default=False)
    valor_pago = _dinheiro(null=True, blank=True)
    pago_em = models.DateField(null=True, blank=True)
    cancelado = models.BooleanField(default=False)
