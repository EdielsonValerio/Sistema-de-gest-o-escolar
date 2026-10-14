from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.views.generic import TemplateView

from .models import ConfiguracaoGeral
from .tenancy import instituicao_do_usuario


class AcessoMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Login obrigatório + permissão verificada no servidor.

    Autenticado sem permissão recebe 403 (e não redirecionamento ao login).
    Se `permission_required` for vazio, basta estar autenticado.
    """

    def has_permission(self):
        if not self.permission_required:
            return True
        return super().has_permission()

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()

    @cached_property
    def instituicao(self):
        return instituicao_do_usuario(self.request.user)


class PaginacaoMixin:
    """Itens por página vêm das configurações gerais da instituição."""

    def get_paginate_by(self, queryset):
        cfg = ConfiguracaoGeral.objects.filter(instituicao=self.instituicao).first()
        return cfg.itens_por_pagina if cfg else 20


class InstituicaoQuerysetMixin(AcessoMixin):
    """Filtra o queryset pela instituição do usuário (isolamento entre instituições)."""

    def get_queryset(self):
        return super().get_queryset().filter(instituicao=self.instituicao)


class InstituicaoFormMixin:
    """ModelForm/Form que recebe `instituicao` e a aplica ao salvar."""

    def __init__(self, *args, instituicao=None, **kwargs):
        self.instituicao = instituicao
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        obj = super().save(commit=False)
        if hasattr(obj, "instituicao_id") and not obj.instituicao_id:
            obj.instituicao = self.instituicao
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class FormKwargsInstituicaoMixin:
    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["instituicao"] = self.instituicao
        return kw


class BootstrapFormMixin:
    """Aplica classes Bootstrap 5 aos widgets."""

    def _aplicar_bootstrap(self):
        for campo in self.fields.values():
            if isinstance(campo, forms.DateField) and not isinstance(campo, forms.DateTimeField):
                campo.widget = forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"})
            w = campo.widget
            cls = w.attrs.get("class", "")
            tipo = w.__class__.__name__
            if tipo in ("CheckboxInput",):
                w.attrs["class"] = (cls + " form-check-input").strip()
            elif tipo in ("Select", "SelectMultiple", "NullBooleanSelect"):
                w.attrs["class"] = (cls + " form-select").strip()
            elif tipo in ("ClearableFileInput", "FileInput"):
                w.attrs["class"] = (cls + " form-control").strip()
            else:
                w.attrs["class"] = (cls + " form-control").strip()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._aplicar_bootstrap()


class SecoesMixin:
    """Permite agrupar campos em seções: SECOES = [("Título", ["campo1", "campo2"]), ...]."""

    SECOES = []

    @property
    def secoes(self):
        return [(t, [self[n] for n in campos if n in self.fields]) for t, campos in self.SECOES]


def _filtrar_querysets_por_instituicao(form):
    """Campos de seleção só oferecem registros da instituição do usuário (isolamento)."""
    if not form.instituicao:
        return
    for campo in form.fields.values():
        qs = getattr(campo, "queryset", None)
        if qs is None:
            continue
        nomes = {f.name for f in qs.model._meta.get_fields()}
        if "instituicao" in nomes:
            campo.queryset = qs.filter(instituicao=form.instituicao)
        elif qs.model.__name__ == "SerieOfertada":
            campo.queryset = qs.filter(ano_letivo__instituicao=form.instituicao)


class BaseForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    def __init__(self, *args, instituicao=None, **kwargs):
        self.instituicao = instituicao
        super().__init__(*args, **kwargs)
        _filtrar_querysets_por_instituicao(self)


class BaseModelForm(BootstrapFormMixin, InstituicaoFormMixin, SecoesMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _filtrar_querysets_por_instituicao(self)
        # A instituição é aplicada ao instance ANTES da validação, para que as constraints de unicidade
        # que envolvem `instituicao` sejam checadas no formulário (e não só estourem no banco).
        if any(f.name == "instituicao" for f in self.instance._meta.get_fields()) and self.instituicao:
            if not self.instance.instituicao_id:
                self.instance.instituicao = self.instituicao

    def _get_validation_exclusions(self):
        exclude = super()._get_validation_exclusions()
        if self.instituicao and getattr(self.instance, "instituicao_id", None):
            exclude.discard("instituicao")
        return exclude


class ObjetoFormView(AcessoMixin, TemplateView):
    """Criar/editar um objeto com ModelForm. Sem exclusão: o histórico é preservado (inativar/encerrar)."""

    template_name = "generic/form.html"
    model = None
    form_class = None
    editando = False
    escopo = "instituicao"  # caminho ORM até a instituição; None = cadastro global
    titulo_novo = "Novo registro"
    titulo_editar = "Editar registro"
    subtitulo = ""
    sucesso_url_name = None
    mensagem_sucesso = "Registro salvo."

    def get_queryset(self):
        qs = self.model._default_manager.all()
        return qs.filter(**{self.escopo: self.instituicao}) if self.escopo else qs

    @cached_property
    def objeto(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"]) if self.editando else None

    def get_form(self, data=None, files=None):
        return self.form_class(data, files, instance=self.objeto, instituicao=self.instituicao)

    def get_extra_context(self):
        return {}

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(form=kw.get("form") or self.get_form(), objeto=self.objeto,
                   titulo=self.titulo_editar if self.objeto else self.titulo_novo, subtitulo=self.subtitulo,
                   cancelar_url=reverse(self.sucesso_url_name), **self.get_extra_context())
        return ctx

    def apos_salvar(self, obj, form, novo):
        """Gancho para auditoria e efeitos adicionais (executa na mesma transação)."""

    def get_sucesso_url(self, obj):
        return reverse(self.sucesso_url_name)

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        novo = self.objeto is None
        with transaction.atomic():
            obj = form.save()
            self.apos_salvar(obj, form, novo)
        messages.success(request, self.mensagem_sucesso)
        return redirect(self.get_sucesso_url(obj))
