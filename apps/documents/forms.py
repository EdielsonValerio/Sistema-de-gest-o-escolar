from django import forms

from apps.core.mixins import BaseModelForm

from .models import CabecalhoDocumento, ModeloDocumento
from .variaveis import sanitizar_html, texto_puro, variaveis_desconhecidas


class ModeloDocumentoForm(BaseModelForm):
    class Meta:
        model = ModeloDocumento
        fields = ["nome", "tipo", "destinatario", "usar_cabecalho", "ativo", "conteudo"]
        widgets = {"conteudo": forms.Textarea(attrs={"rows": 14})}

    def clean_conteudo(self):
        html = sanitizar_html(self.cleaned_data["conteudo"])
        if not texto_puro(html):
            raise forms.ValidationError("Escreva o conteúdo do documento.")
        desconhecidas = variaveis_desconhecidas(html)
        if desconhecidas:
            raise forms.ValidationError(
                "Variáveis inexistentes: " + ", ".join(f"[{v}]" for v in desconhecidas)
                + ". Use o painel de variáveis (colchetes que não são variáveis não são permitidos).")
        return html


class CabecalhoDocumentoForm(BaseModelForm):
    LIMITE = 2 * 1024 * 1024

    class Meta:
        model = CabecalhoDocumento
        fields = ["ativo", "exibir_logotipo", "logotipo", "titulo", "linhas", "rodape"]
        widgets = {"linhas": forms.Textarea(attrs={"rows": 4})}

    def clean_logotipo(self):
        arq = self.cleaned_data.get("logotipo")
        if arq and hasattr(arq, "size") and arq.size > self.LIMITE:
            raise forms.ValidationError("O logotipo deve ter no máximo 2 MB.")
        return arq
