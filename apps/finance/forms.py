import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.academic.models import Turma
from apps.core.mixins import BaseForm, BaseModelForm

from .models import Desconto, PlanoFinanceiro, Recebimento, validar_referencia


class PlanoForm(BaseModelForm):
    SECOES = [
        ("Plano", ["nome", "turma", "situacao"]),
        ("Valores e parcelas", ["valor_total", "quantidade_parcelas", "data_primeira_parcela", "dia_vencimento"]),
        ("Multa e juros por atraso", ["aplicar_encargos", "multa_percentual", "juros_mensal_percentual"]),
        ("Observações", ["observacoes"]),
    ]

    class Meta:
        model = PlanoFinanceiro
        fields = ["nome", "turma", "valor_total", "quantidade_parcelas", "data_primeira_parcela", "dia_vencimento",
                  "aplicar_encargos", "multa_percentual", "juros_mensal_percentual",
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
    forma = forms.ChoiceField(label="Forma de pagamento", choices=Recebimento.formas_de_caixa())
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


class DescontoForm(BaseModelForm):
    SECOES = [
        ("Desconto", ["nome", "tipo", "valor", "dia_limite"]),
        ("Regras", ["todos_os_planos", "acumulativo", "ativo"]),
    ]

    class Meta:
        model = Desconto
        fields = ["nome", "tipo", "valor", "dia_limite", "todos_os_planos", "acumulativo", "ativo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["valor"].widget.attrs.update({"step": "0.01", "min": "0.01"})
        self.fields["dia_limite"].widget.attrs.update({"min": "1", "max": "31"})

    def clean_nome(self):
        nome = self.cleaned_data["nome"].strip()
        outros = Desconto.objects.filter(instituicao=self.instituicao, nome__iexact=nome).exclude(pk=self.instance.pk)
        if outros.exists():
            raise forms.ValidationError("Já existe um desconto com este nome.")
        return nome

    def clean(self):
        d = super().clean()
        if d.get("tipo") == Desconto.Tipo.PERCENTUAL and d.get("valor") and d["valor"] > 100:
            self.add_error("valor", "O percentual não pode passar de 100%.")
        if d.get("todos_os_planos") and d.get("ativo"):
            # os automáticos entram juntos em toda cobrança: um não acumulativo não pode conviver com outro
            outros = Desconto.objects.filter(instituicao=self.instituicao, ativo=True, todos_os_planos=True) \
                .exclude(pk=self.instance.pk)
            if outros.exists() and not d.get("acumulativo"):
                self.add_error("acumulativo", "Um desconto automático não acumulativo não pode existir junto com outros "
                                              "descontos automáticos. Desmarque “todos os planos” ou torne-o acumulativo.")
            elif outros.filter(acumulativo=False).exists():
                self.add_error("todos_os_planos", "Já existe um desconto automático não acumulativo (“%s”); ele só pode "
                               "valer sozinho." % outros.filter(acumulativo=False).first().nome)
        return d
