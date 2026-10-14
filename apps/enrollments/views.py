from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, TemplateView

from apps.academic.models import AnoLetivo, Turma
from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, PaginacaoMixin
from apps.finance.models import PlanoFinanceiro
from apps.finance.services import contratar_plano, cronograma_do_plano, matricular_com_cobranca
from apps.students.models import Aluno

from . import services
from .forms import CancelamentoForm, CobrancaForm, ConclusaoForm, TransferenciaForm, VinculoForm
from .models import Matricula

VIGENTES = Matricula.STATUS_VIGENTES


class MatriculaListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Matricula
    permission_required = "enrollments.view_matricula"
    template_name = "enrollments/lista.html"

    def get_queryset(self):
        g = self.request.GET
        qs = (super().get_queryset().select_related("aluno", "turma__serie_ofertada__serie_referencia", "ano_letivo")
              .order_by("-criado_em", "-id"))
        if g.get("q"):
            qs = qs.filter(Q(aluno__nome__icontains=g["q"]) | Q(aluno__codigo__icontains=g["q"])
                           | Q(numero__icontains=g["q"]))
        if g.get("tipo") in ("OFICIAL", "NAO_OFICIAL"):
            qs = qs.filter(tipo=g["tipo"])
        if g.get("status") in Matricula.Status.values:
            qs = qs.filter(status=g["status"])
        if g.get("ano"):
            qs = qs.filter(ano_letivo_id=g["ano"])
        if g.get("turma"):
            qs = qs.filter(turma_id=g["turma"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(filtros=g, anos=AnoLetivo.objects.filter(instituicao=self.instituicao),
                   turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("-ano_letivo__ano", "nome"),
                   status_opcoes=Matricula.Status.choices,
                   tem_filtro=any(g.get(k) for k in ("q", "tipo", "status", "ano", "turma")))
        return ctx


def _preview(plano):
    return cronograma_do_plano(plano) if plano else []


def _totais(parcelas):
    from decimal import Decimal
    return {k: sum((p[k] for p in parcelas), Decimal("0.00")) for k in ("valor_original", "desconto", "valor_devido")}


class VincularTurmaView(AcessoMixin, TemplateView):
    """Fluxo: tipo → turma → plano/responsável → prévia das parcelas → confirmação."""

    permission_required = "enrollments.add_matricula"
    template_name = "enrollments/vincular.html"

    def get_aluno(self):
        return get_object_or_404(Aluno, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def _turma(self, valor):
        if not valor:
            return None
        return get_object_or_404(Turma.objects.select_related("serie_ofertada__serie_referencia", "ano_letivo"),
                                 pk=valor, instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aluno = self.get_aluno()
        g = self.request.GET
        tipo = g.get("tipo", "OFICIAL")
        tipo = tipo if tipo in ("OFICIAL", "NAO_OFICIAL") else "OFICIAL"
        turma = kw.get("turma") or self._turma(g.get("turma"))
        ctx.update(aluno=aluno, tipo=tipo, turma=turma)
        if turma is None:
            vigentes = set(aluno.matriculas.filter(status__in=VIGENTES).values_list("turma_id", flat=True))
            turmas = (Turma.objects.filter(instituicao=self.instituicao, tipo=tipo).exclude(situacao="ENCERRADA")
                      .select_related("serie_ofertada__serie_referencia", "ano_letivo")
                      .annotate(ocupacao=Count("matriculas", filter=Q(matriculas__status__in=VIGENTES), distinct=True))
                      .order_by("-ano_letivo__ano", "-ano_referencia", "nome"))
            if tipo == "OFICIAL":
                turmas = turmas.exclude(ano_letivo__situacao="ENCERRADO")
            for t in turmas:
                t.ja_vinculado = t.pk in vigentes
                t.lotada = bool(t.capacidade and t.ocupacao >= t.capacidade)
            ctx["turmas"] = turmas
            return ctx
        planos = list(PlanoFinanceiro.objects.filter(turma=turma, situacao="ATIVO").order_by("nome"))
        plano_sel = next((p for p in planos if str(p.pk) == (kw.get("plano_id") or g.get("plano"))), None)
        form = kw.get("form") or VinculoForm(instituicao=self.instituicao, turma=turma, aluno=aluno,
                                             initial={"plano": plano_sel.pk if plano_sel else None})
        user = self.request.user
        previa = _preview(plano_sel)
        ctx.update(planos=planos, plano_sel=plano_sel, form=form, parcelas_previa=previa, total_previa=_totais(previa),
                   qs_base=f"tipo={tipo}&turma={turma.pk}&", pode_gerar=user.has_perm("finance.gerar_cobrancas"),
                   pode_sem_cobranca=user.has_perm("finance.matricular_sem_cobranca"))
        return ctx

    def post(self, request, *args, **kwargs):
        aluno = self.get_aluno()
        turma = self._turma(request.POST.get("turma"))
        if turma is None:
            raise PermissionDenied
        form = VinculoForm(request.POST, instituicao=self.instituicao, turma=turma, aluno=aluno)
        plano_id = request.POST.get("plano")
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, turma=turma, plano_id=plano_id))
        d = form.cleaned_data
        plano, resp = d["plano"], d["responsavel"]
        tem_planos = PlanoFinanceiro.objects.filter(turma=turma, situacao="ATIVO").exists()
        if plano and not request.user.has_perm("finance.gerar_cobrancas"):
            raise PermissionDenied
        if tem_planos and not plano and not request.user.has_perm("finance.matricular_sem_cobranca"):
            messages.error(request, "Escolha um plano financeiro: você não tem permissão para matricular sem cobranças.")
            return self.render_to_response(self.get_context_data(form=form, turma=turma, plano_id=plano_id))
        try:
            matricula, contrato = matricular_com_cobranca(
                aluno=aluno, turma=turma, usuario=request.user, plano=plano, responsavel=resp,
                data_ingresso=d["data_ingresso"], sem_cobranca=bool(tem_planos and not plano))
        except ValidationError as exc:
            ja = aluno.matriculas.filter(turma=turma, status__in=VIGENTES).first()
            if ja and "já possui vínculo" in " ".join(exc.messages):
                messages.info(request, "Este vínculo já foi registrado (solicitação repetida ignorada).")
                return redirect(f"{reverse('students:aluno_ficha', args=[aluno.pk])}?aba=matriculas")
            for m in exc.messages:
                messages.error(request, m)
            return self.render_to_response(self.get_context_data(form=form, turma=turma, plano_id=plano_id))
        msg = f"Vínculo registrado na turma {turma.nome}."
        if matricula.numero:
            msg += f" Número: {matricula.numero}."
        if contrato:
            msg += f" {contrato.quantidade_parcelas} parcelas geradas."
        messages.success(request, msg)
        return redirect(f"{reverse('students:aluno_ficha', args=[aluno.pk])}?aba={'financeiro' if contrato else 'matriculas'}")


class GerarCobrancasView(AcessoMixin, TemplateView):
    """Gera cobranças para uma matrícula vigente que ainda não tem contrato."""

    permission_required = "finance.gerar_cobrancas"
    template_name = "enrollments/cobrancas.html"

    def get_matricula(self):
        return get_object_or_404(Matricula.objects.select_related("aluno", "turma", "ano_letivo"),
                                 pk=self.kwargs["pk"], instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        m = self.get_matricula()
        planos = list(PlanoFinanceiro.objects.filter(turma=m.turma, situacao="ATIVO").order_by("nome"))
        plano_sel = next((p for p in planos if str(p.pk) == self.request.GET.get("plano", kw.get("plano_id"))), None)
        form = kw.get("form") or CobrancaForm(instituicao=self.instituicao, turma=m.turma, aluno=m.aluno,
                                              initial={"plano": plano_sel.pk if plano_sel else None})
        previa = _preview(plano_sel)
        ctx.update(matricula=m, planos=planos, plano_sel=plano_sel, form=form, parcelas_previa=previa,
                   total_previa=_totais(previa), qs_base="", ja_tem=hasattr(m, "contrato"))
        return ctx

    def post(self, request, *args, **kwargs):
        m = self.get_matricula()
        form = CobrancaForm(request.POST, instituicao=self.instituicao, turma=m.turma, aluno=m.aluno)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form, plano_id=request.POST.get("plano")))
        try:
            contrato = contratar_plano(matricula=m, plano=form.cleaned_data["plano"],
                                       responsavel=form.cleaned_data["responsavel"], usuario=request.user)
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
            return self.render_to_response(self.get_context_data(form=form, plano_id=request.POST.get("plano")))
        messages.success(request, f"{contrato.quantidade_parcelas} parcelas geradas.")
        return redirect(f"{reverse('students:aluno_ficha', args=[m.aluno_id])}?aba=financeiro")


class MatriculaAcaoView(AcessoMixin, TemplateView):
    """Ativar, concluir, cancelar ou transferir (com justificativa, quando aplicável)."""

    template_name = "generic/confirm.html"
    PERMISSOES = {"ativar": "enrollments.change_matricula", "concluir": "enrollments.change_matricula",
                  "cancelar": "enrollments.cancelar_matricula", "transferir": "enrollments.transferir_matricula"}
    TITULOS = {"ativar": "Ativar matrícula", "concluir": "Concluir matrícula", "cancelar": "Cancelar matrícula",
               "transferir": "Transferir de turma"}

    def dispatch(self, request, *args, **kwargs):
        acao = kwargs["acao"]
        if acao not in self.PERMISSOES:
            from django.http import Http404
            raise Http404
        self.permission_required = self.PERMISSOES[acao]
        return super().dispatch(request, *args, **kwargs)

    def get_matricula(self):
        return get_object_or_404(Matricula.objects.select_related("aluno", "turma"), pk=self.kwargs["pk"],
                                 instituicao=self.instituicao)

    def get_form(self, m, data=None):
        acao = self.kwargs["acao"]
        if acao == "transferir":
            return TransferenciaForm(data, instituicao=self.instituicao, matricula=m)
        if acao == "cancelar":
            f = CancelamentoForm(data, instituicao=self.instituicao)
            if not self.request.user.has_perm("finance.cancelar_parcela"):
                f.fields.pop("cancelar_parcelas")
            return f
        if acao == "concluir":
            return ConclusaoForm(data, instituicao=self.instituicao)
        return None

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        m = self.get_matricula()
        acao = self.kwargs["acao"]
        ctx.update(titulo=self.TITULOS[acao], matricula=m,
                   subtitulo=f"{m.aluno.nome} · {m.turma.nome} · matrícula {m.numero or 's/n'} ({m.get_status_display()})",
                   form=kw.get("form") or self.get_form(m),
                   cancelar_url=f"{reverse('students:aluno_ficha', args=[m.aluno_id])}?aba=matriculas",
                   botao=self.TITULOS[acao], botao_classe="btn-danger" if acao == "cancelar" else "btn-primary")
        return ctx

    def post(self, request, *args, **kwargs):
        m = self.get_matricula()
        acao = self.kwargs["acao"]
        form = self.get_form(m, request.POST)
        if form is not None and not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data if form is not None else {}
        try:
            if acao == "ativar":
                services.ativar_matricula(m, request.user)
            elif acao == "concluir":
                services.concluir_matricula(m, request.user, d.get("data"))
            elif acao == "cancelar":
                services.cancelar_matricula(m, request.user, d["justificativa"], d.get("data"),
                                            cancelar_parcelas_em_aberto=bool(d.get("cancelar_parcelas")))
            else:
                services.transferir_matricula(m, d["nova_turma"], request.user, d["justificativa"], d.get("data"))
        except ValidationError as exc:
            for msg in exc.messages:
                messages.error(request, msg)
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, "Operação realizada.")
        return redirect(f"{reverse('students:aluno_ficha', args=[m.aluno_id])}?aba=matriculas")
