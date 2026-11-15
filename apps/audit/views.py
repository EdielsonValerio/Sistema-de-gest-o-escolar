from django.contrib.auth.models import User
from django.views.generic import ListView

from apps.core.mixins import AcessoMixin, PaginacaoMixin

from .models import RegistroAuditoria


class AuditoriaListView(PaginacaoMixin, AcessoMixin, ListView):
    permission_required = "audit.ver_auditoria"
    template_name = "audit/lista.html"

    def get_queryset(self):
        g = self.request.GET
        qs = RegistroAuditoria.objects.filter(instituicao=self.instituicao).select_related("usuario")
        if g.get("entidade"):
            qs = qs.filter(entidade=g["entidade"])
        if g.get("acao"):
            qs = qs.filter(acao__icontains=g["acao"])
        if g.get("usuario", "").isdigit():
            qs = qs.filter(usuario_id=g["usuario"])
        if g.get("aluno", "").isdigit():
            qs = qs.filter(aluno_id=g["aluno"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        base = RegistroAuditoria.objects.filter(instituicao=self.instituicao)
        ctx.update(filtros=g, entidades=base.order_by().values_list("entidade", flat=True).distinct(),
                   usuarios=User.objects.filter(perfil__instituicao=self.instituicao).order_by("username"),
                   tem_filtro=any(g.get(k) for k in ("entidade", "acao", "usuario", "aluno")))
        return ctx


class EventosSegurancaView(PaginacaoMixin, AcessoMixin, ListView):
    """Eventos de segurança (bloqueios de login, falhas de MFA/webhook, acessos negados...).

    Administrador do sistema vê todos; Administrador da escola vê somente os da própria escola."""

    exige_escola = False
    permission_required = "audit.ver_auditoria"
    template_name = "audit/eventos.html"

    def has_permission(self):
        u = self.request.user
        if u.is_superuser:
            return True
        return super().has_permission() and getattr(self.request, "instituicao", None) is not None

    def get_paginate_by(self, queryset):
        return 50

    def get_queryset(self):
        from .models import EventoSeguranca
        qs = EventoSeguranca.objects.select_related("usuario", "instituicao")
        if not self.request.user.is_superuser:
            qs = qs.filter(instituicao=self.request.instituicao)
        tipo = self.request.GET.get("tipo", "")
        return qs.filter(tipo=tipo) if tipo in EventoSeguranca.Tipo.values else qs

    def get_context_data(self, **kw):
        from datetime import timedelta

        from django.db.models import Count
        from django.utils import timezone

        from .models import EventoSeguranca
        ctx = super().get_context_data(**kw)
        base = self.get_queryset().model.objects.all()
        if not self.request.user.is_superuser:
            base = base.filter(instituicao=self.request.instituicao)
        ult24 = base.filter(criado_em__gte=timezone.now() - timedelta(hours=24)).values("tipo").annotate(n=Count("id"))
        rotulos = dict(EventoSeguranca.Tipo.choices)
        ctx.update(tipos=EventoSeguranca.Tipo.choices, filtro_tipo=self.request.GET.get("tipo", ""),
                   resumo=[(rotulos.get(r["tipo"], r["tipo"]), r["n"]) for r in ult24.order_by("-n")],
                   global_=self.request.user.is_superuser)
        return ctx
