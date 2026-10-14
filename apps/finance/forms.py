import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.academic.models import Turma
from apps.core.mixins import BaseForm, BaseModelForm

from .models import PlanoFinanceiro, Recebimento, validar_referencia


class PlanoForm(BaseModelForm):
    SECOES = [
        ("Plano", ["nome", "turma", "situacao"]),
        ("Valores e parcelas", ["valor_total", "quantidade_parcelas", "data_primeira_parcela", "dia_vencimento",
                                "desconto_padrao_percentual"]),
        ("Multa e juros por atraso", ["aplicar_encargos", "multa_percentual", "juros_mensal_percentual"]),
        ("Observações", ["observacoes"]),
    ]

    class Meta:
        model = PlanoFinanceiro
        fields = ["nome", "turma", "valor_total", "quantidade_parcelas", "data_primeira_parcela", "dia_vencimento",
                  "desconto_padrao_percentual", "aplicar_encargos", "multa_percentual", "juros_mensal_percentual",
                  "situacao", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 2})}
        help_texts = {
            "valor_total": "O valor de cada parcela é calculado automaticamente (a diferença de centavos fica na última).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor_total"].widget.attrs.update({"step": "0.01", "min": "0.01"})
        qs = Turma.objects.filter(instituicao=self.instituicao).order_by("-ano_letivo__ano", "nome")
        if self.instance.pk:
            self.fields["turma"].disabled = True  # contratos existentes dependem da turma
        else:
            qs = qs.exclude(situacao="ENCERRADA")
        self.fields["turma"].queryset = qs
        self.fields["turma"].label_from_instance = lambda t: f"{t.nome} ({t.ano_exibicao})"

    def clean(self):
        d = super().clean()
        if d.get("aplicar_encargos") and not (d.get("multa_percentual") or d.get("juros_mensal_percentual")):
            self.add_error("multa_percentual", "Informe a multa e/ou os juros ou desmarque a cobrança de encargos.")
        return d


class AjusteParcelaForm(BaseForm):
    ACOES = [("desconto", "Desconto"), ("vencimento", "Vencimento"), ("valor", "Valor"), ("cancelar", "Cancelar")]

    acao = forms.ChoiceField(choices=ACOES)
    valor = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2)
    data = forms.DateField(required=False)
    justificativa = forms.CharField(max_length=255, error_messages={"required": "Informe a justificativa."})

    def clean(self):
        d = super().clean()
        acao = d.get("acao")
        if acao in ("desconto", "valor") and d.get("valor") is None:
            self.add_error("valor", "Informe o valor.")
        if acao == "vencimento" and not d.get("data"):
            self.add_error("data", "Informe a nova data.")
        return d


class CaixaForm(BaseForm):
    token = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    forma = forms.ChoiceField(label="Forma de pagamento", choices=Recebimento.Forma.choices)
    valor_recebido = forms.DecimalField(label="Valor recebido", required=False, min_value=0, max_digits=12,
                                        decimal_places=2)
    referencia = forms.CharField(label="Referência (NSU/ID PIX/final do cartão)", required=False, max_length=60,
                                 validators=[validar_referencia])

    def clean(self):
        d = super().clean()
        if d.get("forma") == "DINHEIRO" and d.get("valor_recebido") is None:
            self.add_error("valor_recebido", "Informe o valor recebido em dinheiro.")
        return d


class FiltroPeriodoForm(BaseForm):
    data_de = forms.DateField(label="De", required=False)
    data_ate = forms.DateField(label="Até", required=False)

    def clean(self):
        d = super().clean()
        if d.get("data_de") and d.get("data_ate") and d["data_ate"] < d["data_de"]:
            raise ValidationError("A data final não pode ser anterior à inicial.")
        return d
