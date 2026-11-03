import re

from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError

from apps.core.mixins import BootstrapFormMixin, SecoesMixin
from apps.core.models import Instituicao

from .models import PerfilUsuario

from .permissions import PERFIS


LOGIN_RE = re.compile(r"^[\w.@+-]{3,100}$")
ERRO_LOGIN = "Escola, usuário ou senha incorretos."


def validar_login_da_escola(login, instituicao, perfil_editado=None):
    """Login: 3 a 100 caracteres (letras, números e . _ @ + -), único DENTRO da escola."""
    login = (login or "").strip()
    if not LOGIN_RE.match(login):
        raise ValidationError("Use de 3 a 100 caracteres: letras, números e . _ @ + - (sem espaços).")
    qs = PerfilUsuario.objects.filter(instituicao=instituicao, login__iexact=login)
    if perfil_editado is not None:
        qs = qs.exclude(pk=perfil_editado.pk)
    if qs.exists():
        raise ValidationError("Já existe um usuário com este login nesta escola.")
    return login


def validar_senha_confirmada(form, d, usuario_temp):
    s1, s2 = d.get("senha1"), d.get("senha2")
    if s1 or s2:
        if s1 != s2:
            form.add_error("senha2", "As senhas não conferem.")
        else:
            try:
                password_validation.validate_password(s1, usuario_temp)
            except ValidationError as exc:
                form.add_error("senha1", exc)


class LoginForm(BootstrapFormMixin, forms.Form):
    """Escola -> usuário -> senha. O administrador do sistema entra pelo modo `sistema` (sem escola)."""

    escola = forms.ModelChoiceField(label="Escola", queryset=Instituicao.objects.none(),
                                    empty_label="Selecione a escola…", required=False)
    usuario = forms.CharField(label="Usuário", max_length=150)
    senha = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, request=None, sistema=False, **kwargs):
        self.request = request
        self.sistema = sistema
        self.user = None
        super().__init__(*args, **kwargs)
        self.fields["escola"].queryset = Instituicao.objects.filter(ativa=True).order_by("nome")
        self.fields["escola"].required = not sistema
        self.fields["usuario"].widget.attrs.update(autocomplete="username")
        self.fields["senha"].widget.attrs.update(autocomplete="current-password")

    def clean(self):
        d = super().clean()
        if self.errors:
            return d
        escola, login, senha = d.get("escola"), d["usuario"], d["senha"]
        if self.sistema:
            user = authenticate(self.request, username=login, password=senha)
            if user is not None and not (user.is_superuser and user.is_active):
                user = None  # usuários de escola não entram por aqui
        else:
            user = authenticate(self.request, escola=escola, login=login, password=senha)
        if user is None:
            raise ValidationError(ERRO_LOGIN)  # mesma mensagem para qualquer falha (não revela o que existe)
        self.user = user
        return d


class UsuarioForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    username = forms.CharField(label="Usuário (login)", max_length=100,
                               help_text="Único dentro da escola. É o que a pessoa digita ao entrar.")
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
        perfil = getattr(self.usuario_editado, "perfil", None) if self.usuario_editado else None
        return validar_login_da_escola(self.cleaned_data["username"], self.instituicao, perfil)

    def clean(self):
        d = super().clean()
        tmp = self.usuario_editado or User(username=d.get("username", ""), first_name=d.get("first_name", ""))
        validar_senha_confirmada(self, d, tmp)
        if self.usuario_editado and self.usuario_logado and self.usuario_editado.pk == self.usuario_logado.pk:
            if not d.get("is_active"):
                self.add_error("is_active", "Você não pode desativar o próprio usuário.")
            perfil = d.get("perfil")
            if perfil and perfil.name != "Administrador" and not self.usuario_logado.is_superuser:
                self.add_error("perfil", "Você não pode remover o próprio perfil de administrador.")
        return d
