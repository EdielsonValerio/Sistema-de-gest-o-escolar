"""Coordenação pedagógica: painel, alocação de professores, disciplinas, períodos, pendências e fechamento."""
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView, TemplateView

from apps.academic.models import AnoLetivo, Turma
from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, ObjetoFormView

from . import servicos
from .acesso import CoordenacaoMixin
from .forms import (AlocacaoForm, ConfiguracaoDiarioForm, DisciplinaForm, PeriodoForm, TrocarProfessorForm)
from .models import (AlocacaoProfessor, Aula, Avaliacao, ConfiguracaoDiario, Disciplina, FechamentoDiario, PeriodoLetivo,
                     Planejamento)

ATIVA = AlocacaoProfessor.Situacao.ATIVA


class PainelCoordenacaoView(CoordenacaoMixin, TemplateView):
    template_name = "diario/painel_coordenacao.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        e = self.instituicao
        hoje = timezone.localdate()
        turmas = Turma.objects.filter(instituicao=e).exclude(situacao=Turma.Situacao.ENCERRADA)
        com = turmas.filter(alocacoes__situacao=ATIVA).distinct()
        aloc = AlocacaoProfessor.objects.filter(instituicao=e, situacao=ATIVA)
        aulas = Aula.objects.filter(instituicao=e)
        fin = aulas.filter(status=Aula.Status.FINALIZADA)
        n_aloc = aloc.count()
        n_periodos = sum(servicos.periodos_da_alocacao(a).count() for a in aloc.select_related("turma__serie_ofertada", "ano_letivo"))
        fechados = FechamentoDiario.objects.filter(instituicao=e, fechado=True, alocacao__situacao=ATIVA).count()
        av_pend = [a for a in Avaliacao.objects.filter(instituicao=e, alocacao__situacao=ATIVA)
                   .annotate(lancadas=Count("notas", filter=Q(notas__valor__isnull=False)))
                   if a.lancadas < servicos.matriculas_da_turma(a.alocacao.turma).count()]
        preenchidos = aloc.filter(aulas__status=Aula.Status.FINALIZADA).distinct().count()
        pendentes = aloc.filter(aulas__status__in=[Aula.Status.RASCUNHO, Aula.Status.CORRECAO]).distinct().count()
        ctx.update(
            professores=servicos.professores_da_escola(e).count(), turmas=turmas.count(), turmas_com=com.count(),
            turmas_sem=turmas.count() - com.count(), diarios_preenchidos=preenchidos, diarios_pendentes=pendentes,
            sem_registro=n_aloc - preenchidos,
            aulas_30d=fin.filter(data__gte=hoje - timedelta(days=30)).count(), aulas_rascunho=aulas.exclude(
                status=Aula.Status.FINALIZADA).count(), freq_lancadas=fin.count(),
            avaliacoes_pendentes=len(av_pend), fechamentos=fechados, fechamentos_total=n_aloc * n_periodos)
        c = ctx
        ctx["cards"] = [
            ("Professores", c["professores"], reverse("diario:alocacoes")),
            ("Turmas", c["turmas"], reverse("diario:alocacoes")),
            ("Turmas com professor", c["turmas_com"], reverse("diario:alocacoes")),
            ("Turmas sem professor (alocação pendente)", c["turmas_sem"], reverse("diario:pendencias")),
            ("Diários com aulas registradas", c["diarios_preenchidos"], reverse("diario:aulas_coordenacao")),
            ("Diários pendentes (rascunho/correção)", c["diarios_pendentes"], reverse("diario:pendencias")),
            ("Diários sem nenhum registro", c["sem_registro"], reverse("diario:pendencias")),
            ("Aulas nos últimos 30 dias", c["aulas_30d"], reverse("diario:aulas_coordenacao")),
            ("Aulas em rascunho/correção", c["aulas_rascunho"], reverse("diario:aulas_coordenacao")),
            ("Frequências lançadas (aulas finalizadas)", c["freq_lancadas"], reverse("diario:relatorio", args=["frequencia-turma"])),
            ("Avaliações com notas pendentes", c["avaliacoes_pendentes"], reverse("diario:relatorios")),
            (f"Fechamentos ({c['fechamentos']} de {c['fechamentos_total']})", c["fechamentos"], reverse("diario:fechamento")),
        ]
        return ctx


# ----------------------------------------------------------------------------- alocação
class AlocacaoListView(InstituicaoQuerysetMixin, ListView):
    model = AlocacaoProfessor
    permission_required = "diario.gerenciar_alocacoes"
    template_name = "diario/alocacao_lista.html"
    paginate_by = 30

    def get_queryset(self):
        qs = (super().get_queryset().select_related("turma", "disciplina", "ano_letivo", "professor", "professor__perfil"))
        g = self.request.GET
        if g.get("situacao") in ("ATIVA", "ENCERRADA"):
            qs = qs.filter(situacao=g["situacao"])
        elif not g.get("situacao"):
            qs = qs.filter(situacao=ATIVA)  # "TODAS" mostra também as encerradas (histórico)
        if g.get("turma", "").isdigit():
            qs = qs.filter(turma_id=g["turma"])
        if g.get("professor", "").isdigit():
            qs = qs.filter(professor_id=g["professor"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("nome"),
                   professores=servicos.professores_da_escola(self.instituicao, so_ativos=False),
                   f=self.request.GET, nome_do=servicos.nome_do)
        return ctx


class AlocacaoCreateView(AcessoMixin, TemplateView):
    permission_required = "diario.gerenciar_alocacoes"
    template_name = "generic/form.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(titulo="Alocar professor", subtitulo="Defina o professor de uma disciplina em uma turma.",
                   form=kw.get("form") or AlocacaoForm(instituicao=self.instituicao, initial=self.request.GET.dict()),
                   cancelar_url=reverse("diario:alocacoes"), botao="Confirmar alocação")
        return ctx

    def post(self, request, *args, **kwargs):
        form = AlocacaoForm(request.POST, instituicao=self.instituicao)
        if form.is_valid():
            d = form.cleaned_data
            try:
                servicos.alocar(escola=self.instituicao, ano=d["ano_letivo"], turma=d["turma"], disciplina=d["disciplina"],
                                professor=d["professor"], por=request.user)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, "Professor alocado.")
                return redirect("diario:alocacoes")
        return self.render_to_response(self.get_context_data(form=form))


class _AlocacaoAcao(AcessoMixin, View):
    permission_required = "diario.gerenciar_alocacoes"

    def get_alocacao(self):
        return get_object_or_404(AlocacaoProfessor, pk=self.kwargs["pk"], instituicao=self.instituicao)


class AlocacaoEncerrarView(_AlocacaoAcao):
    def post(self, request, pk):
        try:
            servicos.encerrar_alocacao(self.get_alocacao(), request.user)
            messages.success(request, "Alocação encerrada. O histórico foi preservado.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("diario:alocacoes")


class AlocacaoTrocarView(AcessoMixin, TemplateView):
    permission_required = "diario.gerenciar_alocacoes"
    template_name = "generic/form.html"

    def get_alocacao(self):
        return get_object_or_404(AlocacaoProfessor, pk=self.kwargs["pk"], instituicao=self.instituicao,
                                 situacao=ATIVA)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        a = self.get_alocacao()
        ctx.update(titulo="Trocar professor", subtitulo=f"{a.disciplina} — {a.turma}. O professor atual fica no histórico.",
                   form=kw.get("form") or TrocarProfessorForm(instituicao=self.instituicao),
                   cancelar_url=reverse("diario:alocacoes"), botao="Trocar professor")
        return ctx

    def post(self, request, *args, **kwargs):
        a = self.get_alocacao()
        form = TrocarProfessorForm(request.POST, instituicao=self.instituicao)
        if form.is_valid():
            try:
                servicos.trocar_professor(a, form.cleaned_data["professor"], request.user)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, "Professor alterado.")
                return redirect("diario:alocacoes")
        return self.render_to_response(self.get_context_data(form=form))


# ----------------------------------------------------------------------------- disciplinas, períodos e critérios
class DisciplinaListView(InstituicaoQuerysetMixin, ListView):
    model = Disciplina
    permission_required = "diario.coordenar_diario"
    template_name = "diario/disciplina_lista.html"


class DisciplinaFormView(CoordenacaoMixin, ObjetoFormView):
    model = Disciplina
    form_class = DisciplinaForm
    sucesso_url_name = "diario:disciplinas"
    titulo_novo, titulo_editar = "Nova disciplina", "Editar disciplina"
    mensagem_sucesso = "Disciplina salva."


class DisciplinaCreateView(DisciplinaFormView):
    editando = False


class DisciplinaUpdateView(DisciplinaFormView):
    editando = True


class PeriodoListView(InstituicaoQuerysetMixin, ListView):
    model = PeriodoLetivo
    permission_required = "diario.coordenar_diario"
    template_name = "diario/periodo_lista.html"

    def get_queryset(self):
        return super().get_queryset().filter(modelo__isnull=True).select_related("ano_letivo")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["anos"] = AnoLetivo.objects.filter(instituicao=self.instituicao).order_by("-ano")
        return ctx


class PeriodoFormView(CoordenacaoMixin, ObjetoFormView):
    model = PeriodoLetivo
    form_class = PeriodoForm
    sucesso_url_name = "diario:periodos"
    titulo_novo, titulo_editar = "Novo período letivo", "Editar período letivo"
    mensagem_sucesso = "Período salvo."


class PeriodoCreateView(PeriodoFormView):
    editando = False


class PeriodoUpdateView(PeriodoFormView):
    editando = True


class CriarBimestresView(CoordenacaoMixin, View):
    """Cria 4 bimestres dividindo o ano letivo em partes iguais (só se o ano ainda não tem períodos)."""

    def post(self, request, ano_pk):
        ano = get_object_or_404(AnoLetivo, pk=ano_pk, instituicao=self.instituicao)
        if PeriodoLetivo.objects.filter(ano_letivo=ano, modelo__isnull=True).exists():
            messages.error(request, "Este ano letivo já tem períodos.")
            return redirect("diario:periodos")
        dias = (ano.data_fim - ano.data_inicio).days + 1
        passo = dias // 4
        inicio = ano.data_inicio
        for i in range(1, 5):
            fim = ano.data_fim if i == 4 else inicio + timedelta(days=passo - 1)
            PeriodoLetivo.objects.create(instituicao=self.instituicao, ano_letivo=ano, nome=f"{i}º Bimestre", ordem=i,
                                         data_inicio=inicio, data_fim=fim)
            inicio = fim + timedelta(days=1)
        messages.success(request, "4 bimestres criados. Ajuste as datas se precisar.")
        return redirect("diario:periodos")



class AulasAcompanhamentoView(InstituicaoQuerysetMixin, ListView):
    model = Aula
    permission_required = "diario.coordenar_diario"
    template_name = "diario/aulas_coordenacao.html"
    paginate_by = 30

    def get_queryset(self):
        qs = super().get_queryset().select_related("turma", "disciplina", "professor", "professor__perfil", "periodo")
        g = self.request.GET
        for chave, campo in (("turma", "turma_id"), ("professor", "professor_id"), ("disciplina", "disciplina_id")):
            if g.get(chave, "").isdigit():
                qs = qs.filter(**{campo: g[chave]})
        if g.get("status") in ("RASCUNHO", "FINALIZADA", "CORRECAO"):
            qs = qs.filter(status=g["status"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("nome"),
                   professores=servicos.professores_da_escola(self.instituicao, so_ativos=False),
                   disciplinas=Disciplina.objects.filter(instituicao=self.instituicao), f=self.request.GET,
                   nome_do=servicos.nome_do)
        return ctx


class SolicitarCorrecaoView(CoordenacaoMixin, View):
    def post(self, request, pk):
        aula = get_object_or_404(Aula, pk=pk, instituicao=self.instituicao)
        try:
            servicos.solicitar_correcao(aula, request.POST.get("motivo", ""), request.user)
            messages.success(request, "Correção solicitada ao professor.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("diario:aulas_coordenacao")


class PendenciasView(CoordenacaoMixin, TemplateView):
    template_name = "diario/pendencias.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        e = self.instituicao
        aloc = (AlocacaoProfessor.objects.filter(instituicao=e, situacao=ATIVA)
                .select_related("turma", "disciplina", "professor", "professor__perfil")
                .annotate(n_final=Count("aulas", filter=Q(aulas__status="FINALIZADA"), distinct=True),
                          n_rasc=Count("aulas", filter=~Q(aulas__status="FINALIZADA"), distinct=True)))
        ctx["sem_registro"] = [a for a in aloc if a.n_final == 0 and a.n_rasc == 0]
        ctx["com_pendencia"] = [a for a in aloc if a.n_rasc]
        sem_prof = Turma.objects.filter(instituicao=e).exclude(situacao=Turma.Situacao.ENCERRADA).exclude(
            alocacoes__situacao=ATIVA)
        ctx["turmas_sem_professor"] = sem_prof
        ctx["nome_do"] = servicos.nome_do
        return ctx


class FechamentoView(CoordenacaoMixin, TemplateView):
    template_name = "diario/fechamento.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        e = self.instituicao
        fech = {(f.alocacao_id, f.periodo_id): f for f in FechamentoDiario.objects.filter(instituicao=e)}
        linhas = []
        for a in AlocacaoProfessor.objects.filter(instituicao=e, situacao=ATIVA).select_related(
                "turma", "turma__serie_ofertada", "disciplina", "professor", "professor__perfil", "ano_letivo"):
            cel = []
            for p in servicos.periodos_da_alocacao(a):
                f = fech.get((a.pk, p.pk))
                cel.append({"p": p, "f": f, "fechado": bool(f and f.fechado)})
            linhas.append({"a": a, "cel": cel})
        ctx.update(linhas=linhas, nome_do=servicos.nome_do)
        return ctx


class ReabrirDiarioView(CoordenacaoMixin, View):
    def post(self, request, pk):
        f = get_object_or_404(FechamentoDiario, pk=pk, instituicao=self.instituicao)
        try:
            servicos.reabrir_diario(f, request.POST.get("motivo", ""), request.user)
            messages.success(request, "Diário reaberto.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("diario:fechamento")


class PlanejamentosCoordenacaoView(InstituicaoQuerysetMixin, ListView):
    """Consulta (somente leitura) dos planejamentos dos professores."""

    model = Planejamento
    permission_required = "diario.coordenar_diario"
    template_name = "diario/planejamentos_coordenacao.html"
    paginate_by = 30

    def get_queryset(self):
        return (super().get_queryset().select_related("alocacao__turma", "alocacao__disciplina", "alocacao__professor",
                                                      "alocacao__professor__perfil", "periodo")
                .annotate(n_aulas=Count("alocacao__aulas", filter=Q(alocacao__aulas__status="FINALIZADA"), distinct=True))
                .order_by("alocacao__turma__nome", "alocacao__disciplina__nome", "tipo"))
