import calendar
from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Max

from apps.core.mixins import BaseModelForm

from .models import AnoLetivo, SerieOfertada, SerieReferencia, Turma


class AnoLetivoForm(BaseModelForm):
    class Meta:
        model = AnoLetivo
        fields = ["nome", "ano", "data_inicio", "data_fim", "situacao"]

    def clean_ano(self):
        ano = self.cleaned_data["ano"]
        if not 1990 <= ano <= 2200:
            raise ValidationError("Informe um ano válido.")
        return ano

    def clean(self):
        d = super().clean()
        ini, fim = d.get("data_inicio"), d.get("data_fim")
        if ini and fim:
            outros = AnoLetivo.objects.filter(instituicao=self.instituicao, data_inicio__lte=fim, data_fim__gte=ini)
            if self.instance.pk:
                outros = outros.exclude(pk=self.instance.pk)
            if outros.exists():
                raise ValidationError(
                    f"O período se sobrepõe ao ano letivo {outros.first().nome}. "
                    "Anos letivos distintos não podem representar o mesmo período institucional.")
        return d


class SerieReferenciaForm(BaseModelForm):
    class Meta:
        model = SerieReferencia
        fields = ["codigo", "nome", "etapa", "ordem", "codigo_oficial", "fonte_codigo_oficial", "ativa"]
        help_texts = {
            "codigo": "Código INTERNO e estável. Não é um código oficial do MEC.",
            "codigo_oficial": "Opcional. Só preencha com classificação oficial e informe a origem ao lado.",
        }

    def clean(self):
        d = super().clean()
        if d.get("codigo_oficial") and not d.get("fonte_codigo_oficial"):
            self.add_error("fonte_codigo_oficial", "Informe a origem do código oficial.")
        return d


class SerieOfertadaForm(BaseModelForm):
    class Meta:
        model = SerieOfertada
        fields = ["ano_letivo", "serie_referencia", "situacao", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["serie_referencia"].queryset = SerieReferencia.objects.filter(ativa=True)
        if self.instance.pk:  # a oferta é identificada por ano + série: não se troca depois de criada
            self.fields["ano_letivo"].disabled = True
            self.fields["serie_referencia"].disabled = True


def somar_meses_data(base, meses):
    total = base.year * 12 + (base.month - 1) + meses
    ano, mes = divmod(total, 12)
    mes += 1
    dia = min(base.day, calendar.monthrange(ano, mes)[1])
    return base.replace(year=ano, month=mes, day=dia)


class TurmaForm(BaseModelForm):
    duracao_quantidade = forms.IntegerField(
        label="Duração", required=False, min_value=1, max_value=120,
        help_text="Alternativa à data de término: informe a duração e a data de início.")
    duracao_unidade = forms.ChoiceField(label="Unidade da duração", required=False,
                                        choices=[("MESES", "meses"), ("DIAS", "dias")], initial="MESES")

    SECOES = [
        ("Identificação", ["tipo", "nome", "numero", "categoria", "capacidade", "situacao",
                           "exige_numero_matricula"]),
        ("Turma oficial", ["serie_ofertada"]),
        ("Turma não oficial", ["ano_referencia", "data_inicio", "data_fim", "duracao_quantidade",
                               "duracao_unidade"]),
        ("Observações", ["observacoes", "ano_letivo"]),
    ]

    class Meta:
        model = Turma
        fields = ["tipo", "nome", "numero", "serie_ofertada", "ano_letivo", "ano_referencia", "data_inicio",
                  "data_fim", "categoria", "capacidade", "situacao", "exige_numero_matricula", "observacoes"]
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 2}), "ano_letivo": forms.HiddenInput()}
        help_texts = {"numero": "Deixe em branco para gerar automaticamente."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["numero"].required = False
        self.fields["ano_letivo"].required = False
        self.fields["serie_ofertada"].queryset = (
            SerieOfertada.objects.filter(ano_letivo__instituicao=self.instituicao)
            .select_related("ano_letivo", "serie_referencia").exclude(situacao="ENCERRADA")
            | SerieOfertada.objects.filter(pk=self.instance.serie_ofertada_id or 0)
        ).distinct()
        if self.instance.pk:  # tipo, série/ano e número são estáveis depois da criação
            for n in ("tipo", "serie_ofertada", "numero"):
                self.fields[n].disabled = True

    def _proximo_numero(self, tipo, serie, ano_ref):
        qs = Turma.objects.filter(instituicao=self.instituicao, tipo=tipo)
        qs = qs.filter(serie_ofertada=serie) if tipo == Turma.Tipo.OFICIAL else qs.filter(ano_referencia=ano_ref)
        return (qs.aggregate(m=Max("numero"))["m"] or 0) + 1

    def clean(self):
        d = super().clean()
        tipo = d.get("tipo") or self.instance.tipo
        if tipo == Turma.Tipo.OFICIAL:
            serie = d.get("serie_ofertada")
            if not serie:
                self.add_error("serie_ofertada", "Turma oficial exige uma série ofertada.")
                return d
            d["ano_letivo"] = serie.ano_letivo
            d["ano_referencia"] = None
            if not d.get("data_inicio"):
                d["data_inicio"] = serie.ano_letivo.data_inicio
            if not d.get("data_fim"):
                d["data_fim"] = serie.ano_letivo.data_fim
        else:
            d["serie_ofertada"] = None
            d["ano_letivo"] = None
            ini, fim = d.get("data_inicio"), d.get("data_fim")
            qtd, un = d.get("duracao_quantidade"), d.get("duracao_unidade")
            if ini and not fim and qtd:
                fim = (somar_meses_data(ini, qtd) - timedelta(days=1)) if un == "MESES" else (
                    ini + timedelta(days=qtd - 1))
                d["data_fim"] = fim
            if not ini or not d.get("data_fim"):
                self.add_error("data_inicio", "Informe o início e o término (ou a duração).")
            if not d.get("ano_referencia") and ini:
                d["ano_referencia"] = ini.year
        if d.get("data_inicio") and d.get("data_fim") and d["data_fim"] < d["data_inicio"]:
            self.add_error("data_fim", "A data de término não pode ser anterior à data de início.")
        if not d.get("numero") and not self.errors:
            d["numero"] = self.instance.numero or self._proximo_numero(
                tipo, d.get("serie_ofertada"), d.get("ano_referencia"))
        return d

    def clean_capacidade(self):
        c = self.cleaned_data.get("capacidade")
        if c is not None and self.instance.pk:
            ocupadas = self.instance.matriculas.filter(status__in=("PENDENTE", "ATIVA")).count()
            if c < ocupadas:
                raise ValidationError(f"A turma já possui {ocupadas} aluno(s) vigente(s).")
        return c
