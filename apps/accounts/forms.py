from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError

from apps.core.mixins import BootstrapFormMixin, SecoesMixin

from .permissions import PERFIS


class UsuarioForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    username = forms.CharField(label="Usuário (login)", max_length=150)
    first_name = forms.CharField(label="Nome", max_length=150)
    last_name = forms.CharField(label="Sobrenome", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    perfil = forms.ModelChoiceField(label="Perfil de acesso", queryset=Group.objects.none(), empty_label=None)
    is_active = forms.BooleanField(label="Usuário ativo", required=False, initial=True)
    senha1 = forms.CharField(label="Senha", widget=forms.PasswordInput(render_value=False), required=False,
                             strip=False)
    senha2 = forms.CharField(label="Confirmar senha", widget=forms.PasswordInput(render_value=False), required=False,
                             strip=False)

    SECOES = [
        ("Identificação", ["username", "first_name", "last_name", "email"]),
        ("Acesso", ["perfil", "is_active"]),
        ("Senha", ["senha1", "senha2"]),
    ]

    def __init__(self, *args, instituicao=None, usuario_editado=None, usuario_logado=None, **kwargs):
        self.instituicao = instituicao
        self.usuario_editado = usuario_editado
        self.usuario_logado = usuario_logado
        super().__init__(*args, **kwargs)
        self.fields["perfil"].queryset = Group.objects.filter(name__in=PERFIS).order_by("name")
        if usuario_editado is None:
            self.fields["senha1"].required = True
            self.fields["senha2"].required = True
        else:
            self.fields["senha1"].help_text = "Deixe em branco para manter a senha atual."

    def clean_username(self):
        u = self.cleaned_data["username"].strip()
        qs = User.objects.filter(username__iexact=u)
        if self.usuario_editado:
            qs = qs.exclude(pk=self.usuario_editado.pk)
        if qs.exists():
            raise ValidationError("Já existe um usuário com este login.")
        return u

    def clean(self):
        d = super().clean()
        s1, s2 = d.get("senha1"), d.get("senha2")
        if s1 or s2:
            if s1 != s2:
                self.add_error("senha2", "As senhas não conferem.")
            else:
                tmp = self.usuario_editado or User(username=d.get("username", ""), first_name=d.get("first_name", ""))
                try:
                    password_validation.validate_password(s1, tmp)
                except ValidationError as exc:
                    self.add_error("senha1", exc)
        if self.usuario_editado and self.usuario_logado and self.usuario_editado.pk == self.usuario_logado.pk:
            if not d.get("is_active"):
                self.add_error("is_active", "Você não pode desativar o próprio usuário.")
            perfil = d.get("perfil")
            if perfil and perfil.name != "Administrador" and not self.usuario_logado.is_superuser:
                self.add_error("perfil", "Você não pode remover o próprio perfil de administrador.")
        return d
