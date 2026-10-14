from django import forms
from django.core.exceptions import ValidationError

from . import numbering
from .mixins import BaseModelForm
from .models import ConfiguracaoGeral, ConfiguracaoNumeracao, EscopoNumeracao, TipoNumeracao


class ConfiguracaoNumeracaoForm(BaseModelForm):
    class Meta:
        model = ConfiguracaoNumeracao
        fields = ["formato", "separador", "tamanho_sequencial", "escopo", "ativa"]
        help_texts = {
            "formato": "Combine as variáveis entre colchetes. Ex.: [AnoLetivo][NumeroSerie][NumeroTurma][Sequencial]",
            "separador": "Inserido entre variáveis consecutivas (opcional).",
        }

    def clean(self):
        dados = super().clean()
        tipo = self.instance.tipo
        formato, escopo = dados.get("formato"), dados.get("escopo")
        if formato:
            try:
                dados["formato"] = numbering.validar_formato(formato, tipo)
                if escopo:
                    numbering.validar_escopo(dados["formato"], tipo, escopo)
            except ValidationError as exc:
                self.add_error("formato", exc)
        sep = dados.get("separador", "")
        if sep and not all(c in "-_./" for c in sep):
            self.add_error("separador", "Use apenas - _ . ou /")
        return dados

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.tipo in (TipoNumeracao.CODIGO_ALUNO, TipoNumeracao.RECEBIMENTO):
            self.fields["escopo"].choices = [
                c for c in EscopoNumeracao.choices if c[0] in (EscopoNumeracao.INSTITUICAO, EscopoNumeracao.ANO_LETIVO)]
            self.fields["ativa"].widget = forms.HiddenInput()
        elif self.instance.tipo == TipoNumeracao.INSCRICAO_NAO_OFICIAL:
            self.fields["ativa"].label = "gerar número de inscrição em turmas não oficiais"
        else:
            self.fields["ativa"].label = "gerar número de matrícula em turmas oficiais"


class ConfiguracaoGeralForm(BaseModelForm):
    class Meta:
        model = ConfiguracaoGeral
        fields = ["exigir_plano_na_matricula", "itens_por_pagina"]

    def clean_itens_por_pagina(self):
        v = self.cleaned_data["itens_por_pagina"]
        if not 5 <= v <= 200:
            raise ValidationError("Informe um valor entre 5 e 200.")
        return v
