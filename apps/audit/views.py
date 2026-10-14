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
