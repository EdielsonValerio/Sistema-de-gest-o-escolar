from django import forms

from apps.core.mixins import BaseForm

from .models import MODELOS


class IntegracaoForm(BaseForm):
    api_key = forms.CharField(
        label="Chave de API da Anthropic (Claude)", required=False, max_length=300,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Cada escola usa a própria chave e paga direto à Anthropic. Fica criptografada e nunca é exibida de novo. "
                  "Deixe em branco para manter a atual.")
    modelo = forms.ChoiceField(label="Modelo", choices=MODELOS)
    limite_mensal_tokens = forms.IntegerField(
        label="Limite mensal de uso (tokens)", min_value=0, max_value=100_000_000,
        help_text="Trava o assistente quando o consumo do mês atingir este valor. 0 = sem limite.")
    aceitar_termo = forms.BooleanField(
        label="Li e aceito os termos de uso do assistente de IA", required=False)

    def __init__(self, *args, cfg=None, **kwargs):
        self.cfg = cfg
        super().__init__(*args, **kwargs)
        if cfg and cfg.termo_aceito_em:
            self.fields["aceitar_termo"].initial = True
            self.fields["aceitar_termo"].disabled = True

    def clean_api_key(self):
        v = (self.cleaned_data.get("api_key") or "").strip()
        if v and any(c.isspace() for c in v):
            raise forms.ValidationError("A chave de API não pode ter espaços.")
        return v

    def clean(self):
        dados = super().clean()
        tem_termo = bool(self.cfg and self.cfg.termo_aceito_em) or dados.get("aceitar_termo")
        if (dados.get("api_key") or (self.cfg and self.cfg.tem_api_key)) and not tem_termo:
            self.add_error("aceitar_termo", "É necessário aceitar os termos de uso para usar a chave.")
        return dados
