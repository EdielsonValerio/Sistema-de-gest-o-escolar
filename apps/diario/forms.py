from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.academic.models import AnoLetivo, Turma
from apps.core.mixins import BaseForm, BaseModelForm, BootstrapFormMixin

from . import servicos
from .models import Aula, Avaliacao, ComponenteNota, ConfiguracaoDiario, Disciplina, ModeloAvaliacao, PeriodoLetivo, Planejamento


class DisciplinaForm(BaseModelForm):
    class Meta:
        model = Disciplina
        fields = ["nome", "sigla", "ativa"]


class PeriodoForm(BaseModelForm):
    class Meta:
        model = PeriodoLetivo
        fields = ["ano_letivo", "nome", "ordem", "data_inicio", "data_fim"]

    def clean(self):
        d = super().clean()
        if d.get("data_inicio") and d.get("data_fim") and d["data_fim"] < d["data_inicio"]:
            self.add_error("data_fim", "O término deve ser após o início.")
        return d


class ConfiguracaoDiarioForm(BaseModelForm):
    class Meta:
        model = ConfiguracaoDiario
        fields = ["media_aprovacao", "frequencia_minima", "usa_recuperacao", "nota_maxima_padrao"]


class AlocacaoForm(BaseForm):
    ano_letivo = forms.ModelChoiceField(label="Ano letivo", queryset=AnoLetivo.objects.all())
    turma = forms.ModelChoiceField(label="Turma", queryset=Turma.objects.all())
    disciplina = forms.ModelChoiceField(label="Disciplina", queryset=Disciplina.objects.filter(ativa=True))
    professor = forms.ModelChoiceField(label="Professor", queryset=User.objects.none())

    def __init__(self, *args, instituicao=None, **kwargs):
        super().__init__(*args, instituicao=instituicao, **kwargs)
        self.fields["professor"].queryset = servicos.professores_da_escola(instituicao)
        self.fields["professor"].label_from_instance = servicos.nome_do

    def clean(self):
        d = super().clean()
        if all(d.get(k) for k in ("ano_letivo", "turma", "disciplina", "professor")):
            try:
                servicos.validar_alocacao(self.instituicao, d["ano_letivo"], d["turma"], d["disciplina"], d["professor"])
            except ValidationError as exc:
                raise ValidationError(exc.messages)
        return d


class TrocarProfessorForm(BaseForm):
    professor = forms.ModelChoiceField(label="Novo professor", queryset=User.objects.none())

    def __init__(self, *args, instituicao=None, **kwargs):
        super().__init__(*args, instituicao=instituicao, **kwargs)
        self.fields["professor"].queryset = servicos.professores_da_escola(instituicao)
        self.fields["professor"].label_from_instance = servicos.nome_do


class AulaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Aula
        fields = ["data", "horario_inicio", "horario_fim", "quantidade", "conteudo", "metodologia", "atividades",
                  "observacoes"]
        widgets = {"conteudo": forms.Textarea(attrs={"rows": 3}), "metodologia": forms.Textarea(attrs={"rows": 2}),
                   "atividades": forms.Textarea(attrs={"rows": 2}), "observacoes": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for n in ("horario_inicio", "horario_fim"):
            self.fields[n].widget = forms.TimeInput(format="%H:%M", attrs={"type": "time", "class": "form-control"})

    def clean(self):
        d = super().clean()
        if d.get("horario_inicio") and d.get("horario_fim") and d["horario_fim"] <= d["horario_inicio"]:
            self.add_error("horario_fim", "O fim deve ser após o início.")
        return d


class AvaliacaoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Avaliacao
        fields = ["periodo", "componente", "titulo", "tipo", "data", "nota_maxima", "peso"]

    def __init__(self, *args, alocacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.alocacao = alocacao
        self.modelo = servicos.modelo_da_alocacao(alocacao)
        self.fields["periodo"].queryset = servicos.periodos_da_alocacao(alocacao)
        self.fields["periodo"].empty_label = None
        tipos = [(Avaliacao.Tipo.NORMAL, "Avaliação")]
        if self.modelo is None:
            tipos.append((Avaliacao.Tipo.RECUPERACAO, "Recuperação do período"))
            del self.fields["componente"]
        else:
            if self.modelo.usa_rec_parcial:
                tipos.append((Avaliacao.Tipo.RECUPERACAO, "Recuperação da fase"))
            if self.modelo.usa_rec_final:
                tipos.append((Avaliacao.Tipo.REC_FINAL, "Recuperação final"))
            self.fields["componente"].queryset = ComponenteNota.objects.filter(fase__modelo=self.modelo).select_related("fase")
            self.fields["componente"].label_from_instance = lambda c: f"{c.fase.nome} — {c.nome} (até {c.valor_maximo})"
            self.fields["componente"].required = False
            self.fields["componente"].help_text = "Obrigatório nas fases com composição de nota (ex.: Prova, Atividade)."
        self.fields["tipo"].choices = tipos

    def clean(self):
        d = super().clean()
        if self.modelo is not None and d.get("tipo") == Avaliacao.Tipo.NORMAL and d.get("periodo"):
            tem = d["periodo"].componentes.exists()
            c = d.get("componente")
            if tem and c is None:
                self.add_error("componente", "Escolha o componente desta fase.")
            elif c is not None and c.fase_id != d["periodo"].pk:
                self.add_error("componente", "O componente escolhido não pertence ao período selecionado.")
        return d


class PlanejamentoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Planejamento
        fields = ["tipo", "periodo", "conteudos_previstos", "objetivos", "metodologias", "habilidades", "bncc"]
        widgets = {k: forms.Textarea(attrs={"rows": 3}) for k in
                   ("conteudos_previstos", "objetivos", "metodologias", "habilidades")}

    def __init__(self, *args, alocacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["periodo"].queryset = servicos.periodos_da_alocacao(alocacao)
        self.fields["periodo"].required = False

    def clean(self):
        d = super().clean()
        if d.get("tipo") == Planejamento.Tipo.PERIODO and not d.get("periodo"):
            self.add_error("periodo", "Informe o período do planejamento.")
        if d.get("tipo") == Planejamento.Tipo.ANUAL:
            d["periodo"] = None
        return d


# ----------------------------------------------------------------------------- modelos de avaliação (administrador do sistema)
from apps.academic.models import SerieReferencia  # noqa: E402

from . import formulas as _formulas, motor as _motor  # noqa: E402

CAMPOS_PARAMETROS = ["nome", "media_aprovacao", "frequencia_minima", "casas_decimais", "arredondamento", "formula_final",
                     "regra_rec_parcial", "formula_rec_final", "media_aprovacao_rec_final"]
CAMPOS_ESTRUTURA = ["ano_letivo", "tipo_fase", "series", "nota_maxima", "usa_rec_parcial", "usa_rec_final"]


class ModeloAvaliacaoForm(BaseModelForm):
    class Meta:
        model = ModeloAvaliacao
        fields = ["nome", "ano_letivo", "tipo_fase", "series", "nota_maxima", "media_aprovacao", "frequencia_minima",
                  "casas_decimais", "arredondamento", "formula_final", "usa_rec_parcial", "regra_rec_parcial",
                  "usa_rec_final", "formula_rec_final", "media_aprovacao_rec_final"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["series"].queryset = SerieReferencia.objects.filter(ativa=True).order_by("etapa", "ordem")
        self.fields["series"].widget.attrs["size"] = 8
        inst = self.instance
        self.publicado = bool(inst.pk and inst.situacao == ModeloAvaliacao.Situacao.PUBLICADO)
        self.tem_fases = bool(inst.pk and inst.fases.exists())
        if self.publicado or self.tem_fases:
            # estrutura travada: com fases criadas, o tipo de fase e o ano não mudam; publicado, só parâmetros
            for n in (["tipo_fase", "ano_letivo"] if not self.publicado else CAMPOS_ESTRUTURA):
                self.fields.pop(n, None)

    def clean(self):
        d = super().clean()
        n = self.instance.fases.count() if self.instance.pk else 0
        for campo, nomes in (("formula_final", _motor.nomes_formula_final(max(n, 12))), ("formula_rec_final", _motor.NOMES_REC_FINAL)):
            if d.get(campo):
                erro = _formulas.validar(d[campo], nomes)
                if erro:
                    self.add_error(campo, erro)
        nota_max = d.get("nota_maxima") or (self.instance.nota_maxima if self.instance.pk else None)
        if d.get("media_aprovacao") is not None and nota_max and d["media_aprovacao"] > nota_max:
            self.add_error("media_aprovacao", "A nota mínima não pode ser maior que a nota máxima.")
        return d


class FaseForm(BaseModelForm):
    class Meta:
        model = PeriodoLetivo
        fields = ["nome", "ordem", "data_inicio", "data_fim", "peso", "formula"]

    def __init__(self, *args, modelo=None, **kwargs):
        self.modelo = modelo
        super().__init__(*args, **kwargs)

    def clean(self):
        d = super().clean()
        if d.get("data_inicio") and d.get("data_fim") and d["data_fim"] < d["data_inicio"]:
            self.add_error("data_fim", "O término deve ser após o início.")
        if d.get("ordem") is not None and PeriodoLetivo.objects.filter(modelo=self.modelo, ordem=d["ordem"]).exclude(
                pk=self.instance.pk).exists():
            self.add_error("ordem", "Já existe uma fase com esta ordem.")
        if d.get("formula") and self.instance.pk:
            comps = list(self.instance.componentes.all())
            erro = (_formulas.validar(d["formula"], _motor.nomes_formula_fase(comps)) if comps
                    else "Cadastre os componentes antes de usar fórmula na fase.")
            if erro:
                self.add_error("formula", erro)
        elif d.get("formula"):
            self.add_error("formula", "Salve a fase e cadastre os componentes antes de definir a fórmula.")
        return d


class ComponenteForm(BaseModelForm):
    class Meta:
        model = ComponenteNota
        fields = ["nome", "sigla", "valor_maximo", "agregacao", "ordem"]

    def __init__(self, *args, fase=None, **kwargs):
        self.fase = fase
        super().__init__(*args, **kwargs)

    def clean_sigla(self):
        s = (self.cleaned_data["sigla"] or "").strip().upper()
        if not _formulas.sigla_valida(s):
            raise ValidationError("Sigla inválida: comece com letra; use letras maiúsculas, números e _; "
                                  "não use nomes reservados (MF, RF, MAX, B1...).")
        if ComponenteNota.objects.filter(fase=self.fase, sigla=s).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Já existe um componente com esta sigla nesta fase.")
        return s


class DuplicarModeloForm(BaseForm):
    ano_letivo = forms.ModelChoiceField(label="Copiar para o ano letivo", queryset=AnoLetivo.objects.all())
    nome = forms.CharField(label="Nome do novo modelo", max_length=80)


class PresetModeloForm(BaseForm):
    modelo_pronto = forms.ChoiceField(label="Modelo pronto", choices=[])
    ano_letivo = forms.ModelChoiceField(label="Ano letivo", queryset=AnoLetivo.objects.all())
    nome = forms.CharField(label="Nome (opcional)", max_length=80, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modelo_pronto"].choices = [(k, v[0]) for k, v in servicos.PRESETS.items()]
