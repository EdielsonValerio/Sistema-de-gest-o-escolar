import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

ZERO = Decimal("0.00")


def _dinheiro(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, **kw)


class PlanoFinanceiro(models.Model):
    class Situacao(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        INATIVO = "INATIVO", "Inativo"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="planos")
    nome = models.CharField("nome do plano", max_length=100)
    turma = models.ForeignKey("academic.Turma", on_delete=models.PROTECT, related_name="planos_financeiros")
    valor_total = _dinheiro(verbose_name="valor total contratado", validators=[MinValueValidator(Decimal("0.01"))])
    quantidade_parcelas = models.PositiveSmallIntegerField(
        "quantidade de parcelas", default=12, validators=[MinValueValidator(1), MaxValueValidator(60)])
    valor_parcela = _dinheiro(verbose_name="valor da parcela (referência)", editable=False, default=ZERO)
    data_primeira_parcela = models.DateField("data da primeira parcela")
    dia_vencimento = models.PositiveSmallIntegerField(
        "dia de vencimento", validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text="Das parcelas seguintes (ajustado ao último dia do mês, se necessário).")
    desconto_padrao_percentual = models.DecimalField(
        "desconto padrão (%)", max_digits=5, decimal_places=2, default=ZERO,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))],
        help_text="Aplicado a cada parcela no momento da geração.")
    aplicar_encargos = models.BooleanField("cobrar multa e juros por atraso", default=False)
    multa_percentual = models.DecimalField("multa (%)", max_digits=5, decimal_places=2, default=ZERO,
                                           validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))])
    juros_mensal_percentual = models.DecimalField("juros ao mês (%)", max_digits=5, decimal_places=2, default=ZERO,
                                                  validators=[MinValueValidator(ZERO),
                                                              MaxValueValidator(Decimal("100"))])
    situacao = models.CharField("situação", max_length=8, choices=Situacao.choices, default=Situacao.ATIVO)
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "plano financeiro"
        verbose_name_plural = "planos financeiros"
        ordering = ["turma__nome", "nome"]
        constraints = [
            models.CheckConstraint(condition=Q(valor_total__gt=0), name="chk_plano_valor_positivo"),
            models.CheckConstraint(condition=Q(quantidade_parcelas__gte=1), name="chk_plano_parcelas"),
            models.CheckConstraint(condition=Q(dia_vencimento__gte=1, dia_vencimento__lte=31),
                                   name="chk_plano_dia_venc"),
        ]

    def __str__(self):
        return f"{self.nome} ({self.turma.nome})"

    def save(self, *args, **kwargs):
        self.valor_parcela = (Decimal(self.valor_total) / self.quantidade_parcelas).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def clean(self):
        if self.turma_id and self.instituicao_id and self.turma.instituicao_id != self.instituicao_id:
            raise ValidationError({"turma": "Turma de outra instituição."})


class ContratoFinanceiro(models.Model):
    """Plano contratado por uma matrícula. Guarda *snapshots*: alterar o plano não altera contratos."""

    class Status(models.TextChoices):
        ATIVO = "ATIVO", "Ativo"
        CANCELADO = "CANCELADO", "Cancelado"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="contratos")
    matricula = models.OneToOneField("enrollments.Matricula", on_delete=models.PROTECT, related_name="contrato")
    plano = models.ForeignKey(PlanoFinanceiro, on_delete=models.PROTECT, related_name="contratos")
    responsavel = models.ForeignKey("students.Responsavel", on_delete=models.PROTECT, related_name="contratos")
    plano_nome = models.CharField(max_length=100)
    valor_total = _dinheiro()
    quantidade_parcelas = models.PositiveSmallIntegerField()
    desconto_padrao_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    aplicar_encargos = models.BooleanField(default=False)
    multa_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    juros_mensal_percentual = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ATIVO)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "contrato financeiro"
        verbose_name_plural = "contratos financeiros"

    def __str__(self):
        return f"Contrato #{self.pk} — {self.plano_nome}"


class ParcelaQuerySet(models.QuerySet):
    def pendentes(self):
        return self.filter(status__in=["ABERTA", "PARCIAL"])

    def vencidas(self, hoje=None):
        return self.pendentes().filter(vencimento__lt=hoje or timezone.localdate())

    def a_vencer(self, hoje=None):
        return self.pendentes().filter(vencimento__gte=hoje or timezone.localdate())


class Parcela(models.Model):
    class Status(models.TextChoices):
        ABERTA = "ABERTA", "Em aberto"
        PARCIAL = "PARCIAL", "Parcialmente pago"
        QUITADA = "QUITADA", "Quitado"
        CANCELADA = "CANCELADA", "Cancelado"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="parcelas")
    aluno = models.ForeignKey("students.Aluno", on_delete=models.PROTECT, related_name="parcelas")
    matricula = models.ForeignKey("enrollments.Matricula", on_delete=models.PROTECT, related_name="parcelas")
    contrato = models.ForeignKey(ContratoFinanceiro, on_delete=models.PROTECT, related_name="parcelas")
    # Responsável no momento da geração. Trocar o responsável do aluno NÃO altera parcelas já emitidas.
    responsavel = models.ForeignKey("students.Responsavel", on_delete=models.PROTECT, related_name="parcelas")
    numero = models.PositiveSmallIntegerField("parcela")
    descricao = models.CharField("descrição", max_length=160)
    valor_original = _dinheiro(verbose_name="valor original")
    desconto = _dinheiro(verbose_name="desconto", default=ZERO)
    acrescimo = _dinheiro(verbose_name="acréscimos", default=ZERO)
    valor_devido = _dinheiro(verbose_name="valor devido")
    valor_pago = _dinheiro(verbose_name="valor pago", default=ZERO)
    emissao = models.DateField("emissão", default=timezone.localdate)
    vencimento = models.DateField("vencimento")
    quitacao = models.DateField("quitação", null=True, blank=True)
    status = models.CharField("situação", max_length=10, choices=Status.choices, default=Status.ABERTA)
    observacoes = models.TextField("observações", blank=True)

    objects = ParcelaQuerySet.as_manager()

    class Meta:
        verbose_name = "parcela"
        verbose_name_plural = "parcelas"
        ordering = ["vencimento", "numero", "id"]
        permissions = [
            ("aplicar_desconto", "Pode aplicar desconto em parcelas"),
            ("alterar_vencimento", "Pode alterar vencimento de parcelas"),
            ("corrigir_valor", "Pode corrigir valor de parcelas"),
            ("cancelar_parcela", "Pode cancelar parcelas"),
            ("gerar_cobrancas", "Pode gerar cobranças na matrícula"),
            ("matricular_sem_cobranca", "Pode concluir matrícula sem gerar cobranças"),
        ]
        indexes = [
            models.Index(fields=["instituicao", "status", "vencimento"]),
            models.Index(fields=["aluno", "status"]),
            models.Index(fields=["responsavel", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["contrato", "numero"], name="uniq_parcela_contrato_numero"),
            models.CheckConstraint(condition=Q(valor_original__gte=0, desconto__gte=0, acrescimo__gte=0,
                                               valor_pago__gte=0), name="chk_parcela_nao_negativos"),
            models.CheckConstraint(condition=Q(desconto__lte=F("valor_original")), name="chk_parcela_desconto"),
            models.CheckConstraint(condition=Q(valor_pago__lte=F("valor_devido")), name="chk_parcela_pago_le_devido"),
        ]

    def __str__(self):
        return f"{self.descricao} — venc. {self.vencimento:%d/%m/%Y}"

    @property
    def saldo(self):
        if self.status == self.Status.CANCELADA:
            return ZERO
        return self.valor_devido - self.valor_pago

    @property
    def vencida(self):
        return self.status in (self.Status.ABERTA, self.Status.PARCIAL) and self.vencimento < timezone.localdate()

    @property
    def situacao_exibicao(self):
        return "Vencido" if self.vencida else self.get_status_display()

    def recalcular_valor_devido(self):
        self.valor_devido = self.valor_original - self.desconto + self.acrescimo


class AjusteParcela(models.Model):
    class Tipo(models.TextChoices):
        DESCONTO = "DESCONTO", "Desconto"
        VENCIMENTO = "VENCIMENTO", "Alteração de vencimento"
        VALOR = "VALOR", "Correção de valor"
        ENCARGOS = "ENCARGOS", "Multa/juros por atraso"
        CANCELAMENTO = "CANCELAMENTO", "Cancelamento"

    parcela = models.ForeignKey(Parcela, on_delete=models.PROTECT, related_name="ajustes")
    tipo = models.CharField(max_length=14, choices=Tipo.choices)
    anterior = models.CharField("valor anterior", max_length=60)
    posterior = models.CharField("valor posterior", max_length=60)
    valor_devido_anterior = _dinheiro()
    valor_devido_posterior = _dinheiro()
    justificativa = models.CharField(max_length=255)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "ajuste de parcela"
        verbose_name_plural = "ajustes de parcela"
        ordering = ["-criado_em", "-id"]


def validar_referencia(valor):
    """Impede gravar número completo de cartão (PAN) no campo de referência."""
    import re
    if re.search(r"(?:\d[ -]?){13,19}", valor or ""):
        raise ValidationError("Não informe o número completo do cartão. Use apenas NSU/ID da transação ou final.")


class Recebimento(models.Model):
    class Forma(models.TextChoices):
        DINHEIRO = "DINHEIRO", "Dinheiro"
        PIX = "PIX", "PIX"
        CREDITO = "CREDITO", "Cartão de crédito"
        DEBITO = "DEBITO", "Cartão de débito"

    class Status(models.TextChoices):
        CONFIRMADO = "CONFIRMADO", "Confirmado"
        ESTORNADO = "ESTORNADO", "Estornado"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="recebimentos")
    numero = models.CharField("número", max_length=30)
    aluno = models.ForeignKey("students.Aluno", on_delete=models.PROTECT, related_name="recebimentos")
    responsavel = models.ForeignKey("students.Responsavel", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="recebimentos")
    forma = models.CharField("forma de pagamento", max_length=10, choices=Forma.choices)
    valor_total = _dinheiro(verbose_name="valor aplicado às parcelas")
    valor_recebido = _dinheiro(verbose_name="valor recebido")
    troco = _dinheiro(verbose_name="troco", default=ZERO)
    referencia = models.CharField("referência (NSU/ID PIX/final do cartão)", max_length=60, blank=True,
                                  validators=[validar_referencia])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CONFIRMADO)
    token_idempotencia = models.UUIDField("token de idempotência", default=uuid.uuid4, unique=True, editable=False)
    operador = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    operador_nome = models.CharField(max_length=150, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "recebimento"
        verbose_name_plural = "recebimentos"
        ordering = ["-criado_em", "-id"]
        permissions = [
            ("registrar_recebimento", "Pode registrar recebimentos (caixa)"),
            ("estornar_recebimento", "Pode estornar recebimentos"),
            ("ver_relatorios", "Pode consultar relatórios financeiros"),
        ]
        indexes = [models.Index(fields=["instituicao", "criado_em"]), models.Index(fields=["aluno"])]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "numero"], name="uniq_recebimento_numero"),
            models.CheckConstraint(condition=Q(valor_total__gt=0), name="chk_recebimento_total_positivo"),
            models.CheckConstraint(condition=Q(valor_recebido__gte=F("valor_total")), name="chk_recebimento_recebido"),
            models.CheckConstraint(condition=Q(troco__gte=0), name="chk_recebimento_troco"),
            models.CheckConstraint(condition=Q(forma="DINHEIRO") | Q(troco=0), name="chk_recebimento_troco_so_dinheiro"),
        ]

    def __str__(self):
        return f"Recebimento {self.numero}"


class AlocacaoRecebimento(models.Model):
    recebimento = models.ForeignKey(Recebimento, on_delete=models.PROTECT, related_name="alocacoes")
    parcela = models.ForeignKey(Parcela, on_delete=models.PROTECT, related_name="alocacoes")
    valor = _dinheiro()
    estornada = models.BooleanField(default=False)

    class Meta:
        verbose_name = "alocação de recebimento"
        verbose_name_plural = "alocações de recebimento"
        constraints = [
            models.UniqueConstraint(fields=["recebimento", "parcela"], name="uniq_alocacao_recebimento_parcela"),
            models.CheckConstraint(condition=Q(valor__gt=0), name="chk_alocacao_positiva"),
        ]


class Estorno(models.Model):
    recebimento = models.OneToOneField(Recebimento, on_delete=models.PROTECT, related_name="estorno")
    justificativa = models.CharField(max_length=255)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    usuario_nome = models.CharField(max_length=150, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "estorno"
        verbose_name_plural = "estornos"
        ordering = ["-criado_em"]
