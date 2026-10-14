from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.views import View
from django.views.generic import TemplateView

from . import numbering
from .forms import ConfiguracaoGeralForm, ConfiguracaoNumeracaoForm
from .mixins import AcessoMixin
from .models import ConfiguracaoGeral, ConfiguracaoNumeracao, TipoNumeracao
from .tenancy import instituicao_do_usuario


class DashboardView(AcessoMixin, TemplateView):
    template_name = "dashboard/dashboard.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        user, inst = self.request.user, self.instituicao
        hoje = timezone.localdate()
        ctx["hoje"] = hoje

        if user.has_perm("students.view_aluno") or user.has_perm("enrollments.view_matricula"):
            from apps.academic.models import AnoLetivo, Turma
            from apps.enrollments.models import Matricula
            from apps.students.models import Aluno

            ano = AnoLetivo.objects.filter(instituicao=inst, situacao="ATIVO").order_by("-ano").first()
            ctx["ano_ativo"] = ano
            vigentes = Matricula.objects.filter(instituicao=inst, status__in=("PENDENTE", "ATIVA"))
            ctx["alunos_ativos"] = Aluno.objects.filter(instituicao=inst, ativo=True).count()
            ctx["alunos_sem_matricula"] = (Aluno.objects.filter(instituicao=inst, ativo=True)
                                           .exclude(matriculas__status__in=("PENDENTE", "ATIVA")).count())
            ctx["matriculas_ativas"] = vigentes.filter(ano_letivo=ano, tipo="OFICIAL").count() if ano else 0
            ctx["turmas_ativas"] = Turma.objects.filter(instituicao=inst, situacao="ATIVA").count()
            ctx["tem_alunos"] = Aluno.objects.filter(instituicao=inst).exists()
            ctx["tem_anos"] = AnoLetivo.objects.filter(instituicao=inst).exists()
            ctx["tem_turmas"] = Turma.objects.filter(instituicao=inst).exists()

        if user.has_perm("finance.view_parcela"):
            from apps.finance.models import Parcela, Recebimento
            pend = Parcela.objects.filter(instituicao=inst).pendentes()
            soma = lambda qs: qs.aggregate(  # noqa: E731
                s=Sum("valor_devido") - Sum("valor_pago"))["s"] or Decimal("0.00")
            ctx["a_receber"] = soma(pend)
            ctx["vencido"] = soma(pend.filter(vencimento__lt=hoje))
            ctx["qtd_vencidas"] = pend.filter(vencimento__lt=hoje).count()
            ctx["recebido_hoje"] = (Recebimento.objects.filter(instituicao=inst, status="CONFIRMADO",
                                                               criado_em__date=hoje)
                                    .aggregate(s=Sum("valor_total"))["s"] or Decimal("0.00"))
            ctx["mostrar_financeiro"] = True
        return ctx


class BuscaRapidaView(AcessoMixin, View):
    permission_required = "students.view_aluno"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        return redirect(f"{reverse('students:aluno_lista')}?q={q}" if q else reverse("students:aluno_lista"))


class NumeracaoListView(AcessoMixin, TemplateView):
    permission_required = "core.view_configuracaonumeracao"
    template_name = "core/numeracao_lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        itens = []
        for tipo in TipoNumeracao:
            cfg = numbering.obter_configuracao(self.instituicao, tipo)
            try:
                exemplos = numbering.prever(cfg.formato, tipo, cfg.separador, cfg.tamanho_sequencial)
            except ValidationError:
                exemplos = []
            itens.append((cfg, exemplos))
        ctx["itens"] = itens
        return ctx


class NumeracaoEditView(AcessoMixin, TemplateView):
    permission_required = "core.change_configuracaonumeracao"
    template_name = "core/numeracao_form.html"

    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def _cfg(self):
        return get_object_or_404(ConfiguracaoNumeracao, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cfg = self._cfg()
        ctx["cfg"] = cfg
        ctx["form"] = kw.get("form") or ConfiguracaoNumeracaoForm(instance=cfg, instituicao=self.instituicao)
        ctx["variaveis"] = [(v, d) for v, d in numbering.VARIAVEIS.items()
                            if v in numbering.VARIAVEIS_POR_TIPO[cfg.tipo]]
        return ctx

    def post(self, request, *args, **kwargs):
        cfg = self._cfg()
        form = ConfiguracaoNumeracaoForm(request.POST, instance=cfg, instituicao=self.instituicao)
        if form.is_valid():
            from apps.audit.services import registrar
            form.save()
            registrar(instituicao=self.instituicao, usuario=request.user, acao="NUMERACAO_ALTERADA",
                      entidade="ConfiguracaoNumeracao", entidade_id=cfg.pk,
                      descricao=f"Formato de {cfg.get_tipo_display()} alterado",
                      dados={"tipo": cfg.tipo, "formato": cfg.formato, "escopo": cfg.escopo})
            messages.success(request, "Configuração salva. Números já emitidos não foram alterados.")
            return redirect("core:numeracao_lista")
        return self.render_to_response(self.get_context_data(form=form))


class NumeracaoPreviaView(AcessoMixin, View):
    """Prévia (JSON) do formato antes de salvar. Não grava nada."""

    permission_required = "core.view_configuracaonumeracao"

    def get(self, request, pk):
        cfg = get_object_or_404(ConfiguracaoNumeracao, pk=pk, instituicao=self.instituicao)
        formato = request.GET.get("formato", "")
        sep = request.GET.get("separador", "")
        try:
            tam = int(request.GET.get("tamanho", cfg.tamanho_sequencial))
            if not 1 <= tam <= 12:
                raise ValueError
        except ValueError:
            return JsonResponse({"ok": False, "erro": "Tamanho do sequencial inválido."})
        if sep and not all(c in "-_./" for c in sep):
            return JsonResponse({"ok": False, "erro": "Separador inválido."})
        try:
            exemplos = numbering.prever(formato, cfg.tipo, sep, tam)
        except ValidationError as exc:
            return JsonResponse({"ok": False, "erro": " ".join(exc.messages)})
        return JsonResponse({"ok": True, "exemplos": exemplos})


class ConfiguracoesGeraisView(AcessoMixin, TemplateView):
    permission_required = "core.change_configuracaogeral"
    template_name = "generic/form.html"

    def _cfg(self):
        cfg, _ = ConfiguracaoGeral.objects.get_or_create(instituicao=self.instituicao)
        return cfg

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(titulo="Configurações gerais", subtitulo="Parâmetros administrativos da instituição.",
                   cancelar_url=reverse("core:dashboard"),
                   form=kw.get("form") or ConfiguracaoGeralForm(instance=self._cfg(), instituicao=self.instituicao))
        return ctx

    def post(self, request, *args, **kwargs):
        form = ConfiguracaoGeralForm(request.POST, instance=self._cfg(), instituicao=self.instituicao)
        if form.is_valid():
            form.save()
            messages.success(request, "Configurações salvas.")
            return redirect("core:configuracoes")
        return self.render_to_response(self.get_context_data(form=form))
