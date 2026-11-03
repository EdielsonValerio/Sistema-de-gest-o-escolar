from datetime import date, timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.mixins import BaseForm, BaseModelForm
from apps.finance.models import validar_referencia
from apps.students.utils import cnpj_valido, cpf_valido, formatar_cnpj, formatar_cpf, so_digitos

from .models import ContaPagar, FormaPagamento
from .services import MAX_REPETICOES


class ContaPagarForm(BaseModelForm):
    repetir = forms.IntegerField(
        label="Repetir por (meses)", min_value=1, max_value=MAX_REPETICOES, initial=1, required=False,
        help_text="1 = conta única. Maior que 1 gera uma conta por mês, com o mesmo valor (ex.: aluguel).")
    token = forms.UUIDField(widget=forms.HiddenInput, required=False)

    SECOES = [
        ("Conta", ["descricao", "categoria", "valor", "vencimento", "repetir"]),
        ("Prestador", ["prestador", "prestador_documento", "numero_documento"]),
        ("Outras informações", ["observacoes"]),
    ]

    class Meta:
        model = ContaPagar
        fields = ["descricao", "categoria", "prestador", "prestador_documento", "numero_documento", "valor",
                  "vencimento", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor"].widget.attrs.update({"step": "0.01", "min": "0.01"})
        self.fields["prestador_documento"].widget.attrs.update({"data-mask": "cpfcnpj"})
        if self.instance.pk:  # edição: não há repetição nem token
            del self.fields["repetir"], self.fields["token"]

    def clean_valor(self):
        v = self.cleaned_data["valor"]
        if v is None or v <= 0:
            raise ValidationError("O valor deve ser maior que zero.")
        return v

    def clean_vencimento(self):
        v = self.cleaned_data["vencimento"]
        hoje = timezone.localdate()
        if v < date(2000, 1, 1) or v > hoje + timedelta(days=3650):
            raise ValidationError("Informe uma data de pagamento válida.")
        return v

    def clean_prestador_documento(self):
        v = self.cleaned_data.get("prestador_documento", "")
        if not v:
            return ""
        n = so_digitos(v)
        if len(n) == 11 and cpf_valido(n):
            return formatar_cpf(n)
        if len(n) == 14 and cnpj_valido(n):
            return formatar_cnpj(n)
        raise ValidationError("CPF ou CNPJ inválido.")

    def clean_repetir(self):
        return self.cleaned_data.get("repetir") or 1


class LiquidarForm(BaseForm):
    data = forms.DateField(label="Data da liquidação")
    valor = forms.DecimalField(label="Valor liquidado", min_value=0, max_digits=12, decimal_places=2,
                               help_text="Padrão: valor da conta. Altere se houve juros, multa ou desconto.")
    forma = forms.ChoiceField(label="Forma de pagamento", choices=FormaPagamento.choices)
    referencia = forms.CharField(label="Referência (comprovante, autenticação, ID PIX)", max_length=60,
                                 required=False, validators=[validar_referencia])
    observacao = forms.CharField(label="Observação", max_length=255, required=False)

    def __init__(self, *args, conta=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor"].widget.attrs.update({"step": "0.01", "min": "0.01"})
        if conta is not None and not self.is_bound:
            self.initial.setdefault("data", timezone.localdate())
            self.initial.setdefault("valor", conta.valor)

    def clean_valor(self):
        v = self.cleaned_data["valor"]
        if v <= 0:
            raise ValidationError("O valor deve ser maior que zero.")
        return v


class JustificativaForm(BaseForm):
    justificativa = forms.CharField(label="Justificativa", min_length=5, max_length=255,
                                    widget=forms.Textarea(attrs={"rows": 3}))
