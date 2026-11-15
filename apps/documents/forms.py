from django import forms

from apps.core.mixins import BaseModelForm

from .models import CabecalhoDocumento, Destinatario, ModeloDocumento
from .tipos import TIPOS_SOMENTE_FICHA
from .variaveis import sanitizar_html, texto_puro, variaveis_desconhecidas


class ModeloDocumentoForm(BaseModelForm):
    class Meta:
        model = ModeloDocumento
        fields = ["nome", "tipo", "destinatario", "usar_cabecalho", "usar_na_matricula", "ativo", "conteudo"]
        widgets = {"conteudo": forms.Textarea(attrs={"rows": 14})}

    def clean(self):
        d = super().clean()
        if d.get("tipo") in TIPOS_SOMENTE_FICHA and d.get("destinatario") != Destinatario.ALUNO:
            self.add_error("destinatario", "Boletim e histórico escolar são sempre emitidos para o aluno.")
        if d.get("tipo") in TIPOS_SOMENTE_FICHA and d.get("usar_na_matricula"):
            self.add_error("usar_na_matricula", "Boletim e histórico são emitidos pelos botões da ficha do aluno.")
        return d

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
        from apps.core.uploads import processar_imagem
        return processar_imagem(self.cleaned_data.get("logotipo"), max_bytes=self.LIMITE, lado_saida=800,
                                rotulo="O logotipo")
