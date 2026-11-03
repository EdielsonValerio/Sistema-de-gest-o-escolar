from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, resolve_url
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import FormView, ListView, TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin
from apps.core.models import Instituicao
from apps.core.tenancy import SESSAO_ESCOLA

from .forms import LoginForm, UsuarioForm
from .models import PerfilUsuario


class LoginView(FormView):
    """Login em duas etapas visuais: 1) escola, 2) usuário e senha. A sessão fica presa à escola escolhida."""

    template_name = "accounts/login.html"
    form_class = LoginForm

    @property
    def modo_sistema(self):
        return (self.request.POST.get("sistema") or self.request.GET.get("sistema")) == "1"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(self.destino_padrao(request.user))
        return super().dispatch(request, *args, **kwargs)

    @staticmethod
    def destino_padrao(user):
        if user.is_superuser and getattr(user, "perfil", None) is None:
            return resolve_url("core:escola_lista")
        return resolve_url("core:dashboard")

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw.update(request=self.request, sistema=self.modo_sistema)
        return kw

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(sistema=self.modo_sistema, next=self.request.POST.get("next") or self.request.GET.get("next", ""),
                   escola_sel=str(self.request.POST.get("escola", "")),
                   escolas=[{"pk": e.pk, "nome": e.nome, "logo": e.logotipo.url if e.logotipo else ""}
                            for e in Instituicao.objects.filter(ativa=True).order_by("nome")])
        return ctx

    def form_valid(self, form):
        user = form.user
        auth_login(self.request, user)
        escola = form.cleaned_data.get("escola")
        self.request.session[SESSAO_ESCOLA] = 0 if self.modo_sistema else escola.pk
        destino = self.request.POST.get("next", "")
        if destino and url_has_allowed_host_and_scheme(destino, allowed_hosts={self.request.get_host()},
                                                       require_https=self.request.is_secure()):
            return redirect(destino)
        return redirect(self.destino_padrao(user))


class LogoutView(auth_views.LogoutView):
    """Logout somente por POST (Django 5)."""


class UsuarioListView(AcessoMixin, ListView):
    permission_required = "accounts.gerenciar_usuarios"
    template_name = "accounts/usuario_lista.html"
    paginate_by = 25

    def get_queryset(self):
        # Superusuários nunca são gerenciados pelo administrador da escola.
        return (User.objects.filter(perfil__instituicao=self.instituicao, is_superuser=False)
                .select_related("perfil").prefetch_related("groups").order_by("perfil__login"))


class UsuarioFormView(AcessoMixin, TemplateView):
    permission_required = "accounts.gerenciar_usuarios"
    template_name = "generic/form.html"
    editando = False

    def get_usuario(self):
        if not self.editando:
            return None
        return get_object_or_404(User, pk=self.kwargs["pk"], perfil__instituicao=self.instituicao,
                                 is_superuser=False)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        usuario = self.get_usuario()
        form = kw.get("form")
        if form is None:
            initial = {}
            if usuario:
                g = usuario.groups.first()
                initial = dict(username=usuario.perfil.login, first_name=usuario.first_name, last_name=usuario.last_name,
                               email=usuario.email, is_active=usuario.is_active, perfil=g)
            form = UsuarioForm(initial=initial, instituicao=self.instituicao, usuario_editado=usuario,
                               usuario_logado=self.request.user)
        ctx.update(form=form, titulo="Editar usuário" if usuario else "Novo usuário",
                   subtitulo="O perfil define o que o usuário pode consultar e executar.",
                   cancelar_url=reverse("accounts:usuario_lista"))
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        usuario = self.get_usuario()
        form = UsuarioForm(request.POST, instituicao=self.instituicao, usuario_editado=usuario,
                           usuario_logado=request.user)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        novo = usuario is None
        if novo:
            usuario = User()
        antes = list(usuario.groups.values_list("name", flat=True)) if not novo else []
        usuario.username = f"{self.instituicao.pk}.{d['username']}"  # interno e global; o login é do perfil
        usuario.first_name = d["first_name"]
        usuario.last_name = d["last_name"]
        usuario.email = d["email"]
        usuario.is_active = d["is_active"]
        if d["senha1"]:
            usuario.set_password(d["senha1"])
        usuario.save()
        if novo:
            PerfilUsuario.objects.create(user=usuario, instituicao=self.instituicao, login=d["username"])
        else:
            PerfilUsuario.objects.filter(user=usuario).update(login=d["username"])
        usuario.groups.set([d["perfil"]])
        registrar(instituicao=self.instituicao, usuario=request.user,
                  acao="USUARIO_CRIADO" if novo else "USUARIO_ALTERADO", entidade="User", entidade_id=usuario.pk,
                  descricao=f"Usuário {d['username']}",
                  dados={"perfil_anterior": antes, "perfil_novo": d["perfil"].name, "ativo": usuario.is_active,
                         "senha_alterada": bool(d["senha1"])})
        messages.success(request, "Usuário salvo.")
        return redirect("accounts:usuario_lista")


class UsuarioCreateView(UsuarioFormView):
    editando = False


class UsuarioUpdateView(UsuarioFormView):
    editando = True
