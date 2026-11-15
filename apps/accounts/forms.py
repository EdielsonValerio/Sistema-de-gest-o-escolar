import re

from django import forms
from django.contrib.auth import authenticate, password_validation
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError

from apps.core.mixins import BootstrapFormMixin, SecoesMixin
from apps.core.models import Instituicao

from .models import PerfilUsuario

from .permissions import PERFIS
from . import seguranca
from .models import TentativaLogin


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
        T = TentativaLogin.Tipo
        k_conta = seguranca.chave_conta(None if self.sistema else escola.pk, login)
        k_ip = seguranca.chave_ip(seguranca.ip_do_pedido(self.request)) if self.request is not None else None
        if seguranca.bloqueado(T.LOGIN_CONTA, k_conta, T.LOGIN_IP, k_ip):
            seguranca.evento("LOGIN_BLOQUEADO", self.request, escola=escola, detalhe="Tentativa durante bloqueio temporário")
            raise ValidationError("Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.")
        if self.sistema:
            user = authenticate(self.request, username=login, password=senha)
            if user is not None and not (user.is_superuser and user.is_active):
                user = None  # usuários de escola não entram por aqui
        else:
            user = authenticate(self.request, escola=escola, login=login, password=senha)
        if user is None:
            seguranca.registrar_falha(T.LOGIN_CONTA, k_conta)
            if k_ip:
                seguranca.registrar_falha(T.LOGIN_IP, k_ip)
            seguranca.evento("LOGIN_FALHA", self.request, escola=escola, detalhe="Credenciais incorretas")
            raise ValidationError(ERRO_LOGIN)  # mesma mensagem para qualquer falha (não revela o que existe)
        seguranca.limpar_falhas(T.LOGIN_CONTA, k_conta)
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
    enviar_email = forms.BooleanField(
        label="Enviar link de ativação por e-mail", required=False,
        help_text="A pessoa recebe um link (uso único, 3 dias) para criar a própria senha. A senha nunca vai por e-mail.")

    SECOES = [
        ("Identificação", ["username", "first_name", "last_name", "email"]),
        ("Acesso", ["perfil", "is_active"]),
        ("Acesso e senha", ["senha1", "senha2", "enviar_email"]),
    ]

    def __init__(self, *args, instituicao=None, usuario_editado=None, usuario_logado=None, **kwargs):
        self.instituicao = instituicao
        self.usuario_editado = usuario_editado
        self.usuario_logado = usuario_logado
        super().__init__(*args, **kwargs)
        self.fields["perfil"].queryset = Group.objects.filter(name__in=PERFIS).order_by("name")
        if usuario_editado is None:
            self.fields["senha1"].help_text = ("Opcional. Em branco, a pessoa cria a senha pelo link de ativação "
                                               "(recomendado). Se definir uma, ela é provisória e deve ser entregue "
                                               "fora do e-mail; a pessoa terá de trocá-la no primeiro acesso.")
        else:
            self.fields["senha1"].help_text = ("Deixe em branco para manter a senha atual. Se definir outra, "
                                               "a pessoa terá de criar uma nova senha no próximo acesso.")

    def clean_username(self):
        perfil = getattr(self.usuario_editado, "perfil", None) if self.usuario_editado else None
        return validar_login_da_escola(self.cleaned_data["username"], self.instituicao, perfil)

    def clean(self):
        d = super().clean()
        tmp = self.usuario_editado or User(username=d.get("username", ""), first_name=d.get("first_name", ""))
        validar_senha_confirmada(self, d, tmp)
        if d.get("enviar_email") and not d.get("email"):
            self.add_error("email", "Informe o e-mail para enviar o acesso.")
        if self.usuario_editado and self.usuario_logado and self.usuario_editado.pk == self.usuario_logado.pk:
            if not d.get("is_active"):
                self.add_error("is_active", "Você não pode desativar o próprio usuário.")
            perfil = d.get("perfil")
            if perfil and perfil.name != "Administrador" and not self.usuario_logado.is_superuser:
                self.add_error("perfil", "Você não pode remover o próprio perfil de administrador.")
        return d


class SenhaTrocarForm(BootstrapFormMixin, forms.Form):
    """Troca de senha pelo próprio usuário (obrigatória no primeiro acesso com senha provisória)."""

    senha_atual = forms.CharField(label="Senha atual (ou provisória)", strip=False, widget=forms.PasswordInput)
    senha1 = forms.CharField(label="Nova senha", strip=False, widget=forms.PasswordInput(render_value=False))
    senha2 = forms.CharField(label="Confirmar nova senha", strip=False, widget=forms.PasswordInput(render_value=False))

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        self.fields["senha_atual"].widget.attrs.update(autocomplete="current-password")
        self.fields["senha1"].widget.attrs.update(autocomplete="new-password")
        self.fields["senha2"].widget.attrs.update(autocomplete="new-password")

    def clean_senha_atual(self):
        if not self.usuario.check_password(self.cleaned_data["senha_atual"]):
            raise ValidationError("A senha atual não confere.")
        return self.cleaned_data["senha_atual"]

    def clean(self):
        d = super().clean()
        s1, s2 = d.get("senha1"), d.get("senha2")
        if s1 and s2:
            if s1 != s2:
                self.add_error("senha2", "As senhas não conferem.")
            elif s1 == d.get("senha_atual"):
                self.add_error("senha1", "A nova senha deve ser diferente da atual.")
            else:
                try:
                    password_validation.validate_password(s1, self.usuario)
                except ValidationError as exc:
                    self.add_error("senha1", exc)
        return d
