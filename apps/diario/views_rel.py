from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin
from apps.enrollments.models import Matricula

from . import relatorios
from .pdf import gerar_pdf


class _RelMixin(AcessoMixin):
    """Coordenação/administração veem a escola; professor vê só o que é dele."""

    def has_permission(self):
        if not super().has_permission():
            return False
        u = self.request.user
        return u.has_perm("diario.coordenar_diario") or u.has_perm("diario.usar_diario_professor")

    @property
    def coordenacao(self):
        return self.request.user.has_perm("diario.coordenar_diario")


def _pdf(request, escola, rel, nome, entidade="Relatorio", ident=""):
    registrar(instituicao=escola, usuario=request.user, acao="RELATORIO_DIARIO_PDF", entidade=entidade, entidade_id=ident,
              descricao=f"PDF: {rel['titulo']}"[:200])
    r = HttpResponse(gerar_pdf(rel, escola.nome), content_type="application/pdf")
    r["Content-Disposition"] = f'inline; filename="{nome}.pdf"'
    return r


class RelatoriosView(_RelMixin, TemplateView):
    template_name = "diario/relatorios.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["lista"] = [(k, v[1]) for k, v in relatorios.RELATORIOS.items() if self.coordenacao or not v[2]]
        return ctx


class RelatorioView(_RelMixin, TemplateView):
    template_name = "diario/relatorio.html"

    def dados(self):
        item = relatorios.RELATORIOS.get(self.kwargs["codigo"])
        if item is None or (item[2] and not self.coordenacao):
            raise Http404
        return item[0](self.request.user, self.instituicao, self.request.GET)

    def get(self, request, *args, **kwargs):
        if request.GET.get("formato") == "pdf":
            return _pdf(request, self.instituicao, self.dados(), self.kwargs["codigo"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(rel=self.dados(), codigo=self.kwargs["codigo"], f=self.request.GET)
        return ctx


class BoletimView(AcessoMixin, TemplateView):
    """Boletim do aluno (todas as disciplinas): coordenação e administração."""

    permission_required = "diario.coordenar_diario"
    template_name = "diario/relatorio.html"

    def rel(self):
        m = get_object_or_404(Matricula.objects.select_related("aluno", "turma"), pk=self.kwargs["matricula_pk"],
                              instituicao=self.instituicao)
        return relatorios.boletim(m)

    def get(self, request, *args, **kwargs):
        if request.GET.get("formato") == "pdf":
            return _pdf(request, self.instituicao, self.rel(), f"boletim-{self.kwargs['matricula_pk']}", "Matricula",
                        self.kwargs["matricula_pk"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(rel=self.rel(), codigo="", boletim=True)
        return ctx
