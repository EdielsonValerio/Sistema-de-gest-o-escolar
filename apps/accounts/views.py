from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin

from .forms import UsuarioForm
from .models import PerfilUsuario


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    """Logout somente por POST (Django 5)."""


class UsuarioListView(AcessoMixin, ListView):
    permission_required = "accounts.gerenciar_usuarios"
    template_name = "accounts/usuario_lista.html"
    paginate_by = 25

    def get_queryset(self):
        return (User.objects.filter(perfil__instituicao=self.instituicao)
                .prefetch_related("groups").order_by("username"))


class UsuarioFormView(AcessoMixin, TemplateView):
    permission_required = "accounts.gerenciar_usuarios"
    template_name = "generic/form.html"
    editando = False

    def get_usuario(self):
        if not self.editando:
            return None
        return get_object_or_404(User, pk=self.kwargs["pk"], perfil__instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        usuario = self.get_usuario()
        form = kw.get("form")
        if form is None:
            initial = {}
            if usuario:
                g = usuario.groups.first()
                initial = dict(username=usuario.username, first_name=usuario.first_name, last_name=usuario.last_name,
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
            usuario = User(username=d["username"])
        antes = list(usuario.groups.values_list("name", flat=True)) if not novo else []
        usuario.username = d["username"]
        usuario.first_name = d["first_name"]
        usuario.last_name = d["last_name"]
        usuario.email = d["email"]
        usuario.is_active = d["is_active"]
        if d["senha1"]:
            usuario.set_password(d["senha1"])
        usuario.save()
        if novo:
            PerfilUsuario.objects.create(user=usuario, instituicao=self.instituicao)
        usuario.groups.set([d["perfil"]])
        registrar(instituicao=self.instituicao, usuario=request.user,
                  acao="USUARIO_CRIADO" if novo else "USUARIO_ALTERADO", entidade="User", entidade_id=usuario.pk,
                  descricao=f"Usuário {usuario.username}",
                  dados={"perfil_anterior": antes, "perfil_novo": d["perfil"].name, "ativo": usuario.is_active,
                         "senha_alterada": bool(d["senha1"])})
        messages.success(request, "Usuário salvo.")
        return redirect("accounts:usuario_lista")


class UsuarioCreateView(UsuarioFormView):
    editando = False


class UsuarioUpdateView(UsuarioFormView):
    editando = True
