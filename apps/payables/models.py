"""Contas a pagar: saídas financeiras da escola, debitadas do saldo total recebido.

Regras de integridade protegidas por constraints: valor positivo, uma única liquidação ativa por conta,
estorno sempre com justificativa e desativação sempre com motivo. Nada é excluído fisicamente.
"""

from django.conf import settings
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from apps.finance.models import validar_referencia



class Categoria(models.TextChoices):
    FOLHA = "FOLHA", "Folha de pagamento e encargos"
    ALUGUEL = "ALUGUEL", "Aluguel e condomínio"
    CONTAS_CONSUMO = "CONSUMO", "Água, energia, internet e telefone"
    MATERIAL = "MATERIAL", "Material didático e de escritório"
    MANUTENCAO = "MANUTENCAO", "Manutenção e reformas"
    SERVICOS = "SERVICOS", "Serviços de terceiros"
    IMPOSTOS = "IMPOSTOS", "Impostos e taxas"
    OUTROS = "OUTROS", "Outras despesas"


class FormaPagamento(models.TextChoices):
    DINHEIRO = "DINHEIRO", "Dinheiro"
    PIX = "PIX", "PIX"
    TRANSFERENCIA = "TRANSFERENCIA", "Transferência bancária"
    BOLETO = "BOLETO", "Boleto"
    CARTAO = "CARTAO", "Cartão"
    CHEQUE = "CHEQUE", "Cheque"
    OUTRA = "OUTRA", "Outra"


def _dinheiro(**kw):
    return models.DecimalField(max_digits=12, decimal_places=2, **kw)


class ContaPagar(models.Model):
    class Status(models.TextChoices):
        ABERTA = "ABERTA", "Em aberto"
        LIQUIDADA = "LIQUIDADA", "Liquidada"
        DESATIVADA = "DESATIVADA", "Desativada"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="contas_pagar")
    numero = models.CharField("número", max_length=20, editable=False)
    descricao = models.CharField("descrição", max_length=150)
    categoria = models.CharField("categoria", max_length=12, choices=Categoria.choices, default=Categoria.OUTROS)
    prestador = models.CharField("prestador / fornecedor", max_length=150)
    prestador_documento = models.CharField("CPF/CNPJ do prestador", max_length=18, blank=True)
    numero_documento = models.CharField("nº do documento (NF, boleto, contrato)", max_length=40, blank=True)
    valor = _dinheiro(verbose_name="valor", validators=[MinValueValidator(Decimal("0.01"))])
    vencimento = models.DateField("data de pagamento (vencimento)")
    observacoes = models.TextField("observações", blank=True)
    status = models.CharField("situação", max_length=10, choices=Status.choices, default=Status.ABERTA)

    # Contas criadas por repetição mensal compartilham o grupo.
    grupo = models.UUIDField(null=True, blank=True, editable=False)
    parcela_numero = models.PositiveSmallIntegerField(default=1, editable=False)
    parcela_total = models.PositiveSmallIntegerField(default=1, editable=False)
    # Evita duplicar o cadastro quando o formulário é enviado duas vezes (clique repetido, falha de conexão).
    token_criacao = models.UUIDField(null=True, blank=True, unique=True, editable=False)

    desativada_em = models.DateTimeField(null=True, blank=True, editable=False)
    desativada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name="+", editable=False)
    motivo_desativacao = models.CharField("motivo da desativação", max_length=255, blank=True, editable=False)

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "conta a pagar"
        verbose_name_plural = "contas a pagar"
        ordering = ["vencimento", "id"]
        permissions = [
            ("liquidar_conta", "Pode liquidar contas a pagar"),
            ("desativar_conta", "Pode desativar e reativar contas a pagar"),
            ("estornar_liquidacao", "Pode estornar liquidações de contas a pagar"),
        ]
        indexes = [models.Index(fields=["instituicao", "status", "vencimento"])]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "numero"], name="uniq_conta_pagar_numero"),
            models.CheckConstraint(condition=Q(valor__gt=0), name="chk_conta_pagar_valor_positivo"),
            models.CheckConstraint(condition=Q(parcela_numero__lte=F("parcela_total")), name="chk_conta_pagar_parcela"),
            models.CheckConstraint(condition=~Q(status="DESATIVADA") | ~Q(motivo_desativacao=""),
                                   name="chk_conta_pagar_desativada_com_motivo"),
        ]

    def __str__(self):
        return f"{self.numero} — {self.descricao}"

    @property
    def vencida(self):
        return self.status == self.Status.ABERTA and self.vencimento < timezone.localdate()

    @property
    def dias_para_vencer(self):
        return (self.vencimento - timezone.localdate()).days

    @property
    def prazo_texto(self):
        """Texto curto para notificações e listas (somente contas em aberto)."""
        if self.status != self.Status.ABERTA:
            return ""
        d = self.dias_para_vencer
        if d < 0:
            return f"Venceu há {-d} dia{'s' if d != -1 else ''}"
        if d == 0:
            return "Vence hoje"
        if d == 1:
            return "Vence amanhã"
        return f"Vence em {d} dias"

    @property
    def liquidacao_atual(self):
        return self.liquidacoes.filter(estornada=False).first()


class Liquidacao(models.Model):
    """Saída de caixa. É o que debita o saldo. O estorno preserva o registro original."""

    INSTITUICAO_DE = "conta"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+", editable=False,
                                    blank=True)
    conta = models.ForeignKey(ContaPagar, on_delete=models.PROTECT, related_name="liquidacoes")
    valor = _dinheiro(verbose_name="valor liquidado", validators=[MinValueValidator(Decimal("0.01"))])
    data = models.DateField("data da liquidação")
    forma = models.CharField("forma de pagamento", max_length=14, choices=FormaPagamento.choices)
    referencia = models.CharField("referência (comprovante, autenticação, ID PIX)", max_length=60, blank=True,
                                  validators=[validar_referencia])
    observacao = models.CharField("observação", max_length=255, blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    usuario_nome = models.CharField(max_length=150, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    estornada = models.BooleanField(default=False)
    estorno_justificativa = models.CharField(max_length=255, blank=True)
    estorno_usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="+")
    estorno_usuario_nome = models.CharField(max_length=150, blank=True)
    estornada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "liquidação de conta a pagar"
        verbose_name_plural = "liquidações de contas a pagar"
        ordering = ["-criado_em", "-id"]
        indexes = [models.Index(fields=["instituicao", "estornada", "data"])]
        constraints = [
            models.UniqueConstraint(fields=["conta"], condition=Q(estornada=False),
                                    name="uniq_liquidacao_ativa_por_conta"),
            models.CheckConstraint(condition=Q(valor__gt=0), name="chk_liquidacao_valor_positivo"),
            models.CheckConstraint(condition=Q(estornada=False) | ~Q(estorno_justificativa=""),
                                   name="chk_liquidacao_estorno_com_justificativa"),
        ]

    def __str__(self):
        return f"Liquidação de {self.conta.numero}"
