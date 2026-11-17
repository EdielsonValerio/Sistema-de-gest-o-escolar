from django import forms

from apps.academic.models import Turma
from apps.students.models import Responsavel

from .models import Comunicado, ConfiguracaoWhatsApp


class ConfiguracaoForm(forms.ModelForm):
    token = forms.CharField(label="Token de acesso permanente", required=False, strip=True,
                            widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
                            help_text="Deixe em branco para manter o token atual. Nunca é exibido de volta.")

    class Meta:
        model = ConfiguracaoWhatsApp
        fields = ["phone_number_id", "waba_id", "template_nome", "template_idioma", "versao_api"]

    def clean_versao_api(self):
        v = self.cleaned_data["versao_api"].strip()
        import re
        if not re.fullmatch(r"v\d{1,2}\.\d", v):
            raise forms.ValidationError("Use o formato v21.0.")
        return v

    def clean_phone_number_id(self):
        v = self.cleaned_data["phone_number_id"].strip()
        if v and not v.isdigit():
            raise forms.ValidationError("O ID do número tem apenas dígitos.")
        return v


class ComunicadoForm(forms.Form):
    alvo = forms.ChoiceField(label="Enviar para", choices=Comunicado.Alvo.choices, widget=forms.RadioSelect,
                             initial=Comunicado.Alvo.TURMAS)
    responsavel = forms.ModelChoiceField(label="Responsável", queryset=Responsavel.objects.none(), required=False)
    turmas = forms.ModelMultipleChoiceField(label="Turmas", queryset=Turma.objects.none(), required=False)
    mensagem = forms.CharField(label="Mensagem", max_length=900, widget=forms.Textarea(attrs={"rows": 5}),
                               help_text="Até 900 caracteres, em um só parágrafo (quebras de linha viram espaço).")

    def __init__(self, *a, instituicao, **kw):
        super().__init__(*a, **kw)
        self.fields["responsavel"].queryset = Responsavel.objects.filter(instituicao=instituicao, ativo=True).order_by("nome")
        self.fields["turmas"].queryset = Turma.objects.filter(instituicao=instituicao, situacao=Turma.Situacao.ATIVA).order_by("nome")

    def clean(self):
        d = super().clean()
        alvo = d.get("alvo")
        if alvo == Comunicado.Alvo.RESPONSAVEL and not d.get("responsavel"):
            self.add_error("responsavel", "Escolha o responsável.")
        if alvo == Comunicado.Alvo.TURMAS and not d.get("turmas"):
            self.add_error("turmas", "Escolha ao menos uma turma.")
        return d
