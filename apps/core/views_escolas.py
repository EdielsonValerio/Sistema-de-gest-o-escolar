"""Área do administrador do sistema (superusuário): cadastro de escolas e de seus administradores."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, TemplateView

from apps.audit.services import registrar

from .forms_escolas import AdministradorForm, EscolaForm, NovaSenhaForm
from .models import Instituicao
from .services_escolas import cadastrar_escola, criar_administrador, redefinir_senha


# Os dois formulários da tela "Nova escola" têm um campo "nome": o prefixo evita colisão no POST e ids duplicados.
PREFIXO_ADMIN = "admin"


class SistemaMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Somente superusuário. Autenticado sem permissão recebe 403 (não é redirecionado ao login)."""

    def test_func(self):
        return self.request.user.is_superuser and self.request.user.is_active

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()


class EscolaListView(SistemaMixin, ListView):
    template_name = "core/escola_lista.html"
    context_object_name = "escolas"
    paginate_by = 25

    def get_queryset(self):
        qs = Instituicao.objects.annotate(qtd_usuarios=Count("perfis", distinct=True),
                                          qtd_alunos=Count("alunos", distinct=True)).order_by("nome")
        q = self.request.GET.get("q", "").strip()
        return qs.filter(nome__icontains=q) if q else qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class EscolaCreateView(SistemaMixin, TemplateView):
    template_name = "core/escola_form.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(titulo="Nova escola", form=kw.get("form") or EscolaForm(),
                   form_admin=kw.get("form_admin") or AdministradorForm(prefix=PREFIXO_ADMIN), escola=None,
                   subtitulo="Cadastre a escola e o administrador que fará as configurações do ambiente dela.")
        return ctx

    def post(self, request, *args, **kwargs):
        form = EscolaForm(request.POST, request.FILES)
        form_admin = AdministradorForm(request.POST, prefix=PREFIXO_ADMIN)
        if not (form.is_valid() and form_admin.is_valid()):  # valida os dois para mostrar todos os erros
            return self.render_to_response(self.get_context_data(form=form, form_admin=form_admin))
        escola, admin = cadastrar_escola(form, form_admin.cleaned_data, request.user)
        messages.success(request, f"Escola “{escola.nome}” cadastrada. O administrador acessa escolhendo a escola "
                                  f"e informando o usuário “{form_admin.cleaned_data['login']}”.")
        return redirect("core:escola_editar", pk=escola.pk)


class EscolaUpdateView(SistemaMixin, TemplateView):
    template_name = "core/escola_form.html"

    @property
    def escola(self):
        return get_object_or_404(Instituicao, pk=self.kwargs["pk"])

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        escola = self.escola
        admins = (User.objects.filter(perfil__instituicao=escola, groups__name="Administrador", is_superuser=False)
                  .select_related("perfil").order_by("perfil__login").distinct())
        ctx.update(titulo=escola.nome, escola=escola, form=kw.get("form") or EscolaForm(instance=escola),
                   administradores=admins, subtitulo="Dados, logotipo e administradores da escola.")
        return ctx

    def post(self, request, *args, **kwargs):
        escola = self.escola
        form = EscolaForm(request.POST, request.FILES, instance=escola)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        with transaction.atomic():
            escola = form.save()
            from apps.assistente.servicos import habilitar_pelo_sistema
            habilitar_pelo_sistema(escola, form.cleaned_data["ia_habilitada"], request.user)
            registrar(instituicao=escola, usuario=request.user, acao="ESCOLA_ALTERADA", entidade="Instituicao",
                      entidade_id=escola.pk, descricao=f"Escola alterada: {escola.nome}",
                      dados={"campos": form.changed_data, "ativa": escola.ativa})
        messages.success(request, "Escola salva.")
        return redirect("core:escola_editar", pk=escola.pk)


class AdministradorCreateView(SistemaMixin, TemplateView):
    template_name = "generic/form.html"

    @property
    def escola(self):
        return get_object_or_404(Instituicao, pk=self.kwargs["pk"])

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        escola = self.escola
        ctx.update(titulo="Novo administrador", subtitulo=f"Escola: {escola.nome}",
                   form=kw.get("form") or AdministradorForm(instituicao=escola),
                   cancelar_url=reverse("core:escola_editar", args=[escola.pk]))
        return ctx

    def post(self, request, *args, **kwargs):
        escola = self.escola
        form = AdministradorForm(request.POST, instituicao=escola)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        with transaction.atomic():
            criar_administrador(escola, login=d["login"], nome=d["nome"], sobrenome=d["sobrenome"],
                                email=d["email"], senha=d["senha1"], criado_por=request.user)
        messages.success(request, "Administrador criado.")
        return redirect("core:escola_editar", pk=escola.pk)


class AdministradorSenhaView(SistemaMixin, TemplateView):
    template_name = "generic/form.html"

    @property
    def usuario(self):
        return get_object_or_404(User, pk=self.kwargs["user_pk"], perfil__instituicao_id=self.kwargs["pk"],
                                 is_superuser=False)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        u = self.usuario
        ctx.update(titulo="Redefinir senha", subtitulo=f"{u.perfil.login} — {u.perfil.instituicao.nome}",
                   form=kw.get("form") or NovaSenhaForm(usuario=u), botao="Redefinir senha",
                   cancelar_url=reverse("core:escola_editar", args=[u.perfil.instituicao_id]))
        return ctx

    def post(self, request, *args, **kwargs):
        u = self.usuario
        form = NovaSenhaForm(request.POST, usuario=u)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        redefinir_senha(u, form.cleaned_data["senha1"], request.user)
        messages.success(request, "Senha redefinida.")
        return redirect("core:escola_editar", pk=u.perfil.instituicao_id)
