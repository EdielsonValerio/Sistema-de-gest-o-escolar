from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.accounts.forms import LOGIN_RE, validar_login_da_escola, validar_senha_confirmada
from apps.students.utils import cnpj_valido, formatar_cnpj, so_digitos

from .mixins import BootstrapFormMixin, SecoesMixin
from .models import Instituicao

LIMITE_LOGOTIPO = 2 * 1024 * 1024


class EscolaForm(BootstrapFormMixin, SecoesMixin, forms.ModelForm):
    ia_habilitada = forms.BooleanField(
        label="Habilitar o assistente de IA (assinatura contratada)", required=False,
        help_text="Libera para a escola a tela de integração do assistente de IA. A escola informa a própria chave "
                  "de API e paga o uso direto ao fornecedor.")

    class Meta:
        model = Instituicao
        fields = ["nome", "cnpj", "logotipo", "ativa"]
        help_texts = {"logotipo": "PNG, JPG ou WebP, até 2 MB. Aparece no menu do sistema e nos documentos."}

    SECOES = [("Dados da escola", ["nome", "cnpj", "logotipo", "ativa"]),
              ("Módulos contratados", ["ia_habilitada"])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            from apps.assistente.models import ConfiguracaoIA
            self.fields["ia_habilitada"].initial = ConfiguracaoIA.objects.filter(
                instituicao=self.instance, habilitada=True).exists()

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        qs = Instituicao.objects.filter(nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Já existe uma escola com este nome.")
        return nome

    def clean_cnpj(self):
        v = self.cleaned_data.get("cnpj", "")
        if not v:
            return ""
        if not cnpj_valido(v):
            raise ValidationError("CNPJ inválido.")
        return formatar_cnpj(so_digitos(v))

    def clean_logotipo(self):
        arq = self.cleaned_data.get("logotipo")
        if arq and hasattr(arq, "size") and arq.size > LIMITE_LOGOTIPO:
            raise ValidationError("O logotipo deve ter no máximo 2 MB.")
        if arq and hasattr(arq, "image") and arq.image.format not in ("PNG", "JPEG", "WEBP"):
            raise ValidationError("Use uma imagem PNG, JPG ou WebP.")
        return arq


class AdministradorForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    nome = forms.CharField(label="Nome", max_length=150)
    sobrenome = forms.CharField(label="Sobrenome", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    login = forms.CharField(label="Usuário (login)", max_length=100,
                            help_text="É o que o administrador digita ao entrar, depois de escolher a escola.")
    senha1 = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput(render_value=False))
    senha2 = forms.CharField(label="Confirmar senha", strip=False, widget=forms.PasswordInput(render_value=False))

    SECOES = [("Administrador da escola", ["nome", "sobrenome", "email", "login"]),
              ("Senha inicial", ["senha1", "senha2"])]

    def __init__(self, *args, instituicao=None, **kwargs):
        self.instituicao = instituicao  # None no cadastro (a escola ainda não existe)
        super().__init__(*args, **kwargs)

    def clean_login(self):
        login = self.cleaned_data["login"].strip()
        if self.instituicao is not None:
            return validar_login_da_escola(login, self.instituicao)
        if not LOGIN_RE.match(login):
            raise ValidationError("Use de 3 a 100 caracteres: letras, números e . _ @ + - (sem espaços).")
        return login

    def clean(self):
        d = super().clean()
        validar_senha_confirmada(self, d, User(username=d.get("login", ""), first_name=d.get("nome", "")))
        return d


class NovaSenhaForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    senha1 = forms.CharField(label="Nova senha", strip=False, widget=forms.PasswordInput(render_value=False))
    senha2 = forms.CharField(label="Confirmar nova senha", strip=False, widget=forms.PasswordInput(render_value=False))

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean(self):
        d = super().clean()
        validar_senha_confirmada(self, d, self.usuario)
        return d
