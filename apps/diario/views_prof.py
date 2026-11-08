"""Ambiente do professor: só turmas/disciplinas alocadas a ele."""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from . import calculos, servicos
from .acesso import ProfessorMixin
from .forms import AulaForm, AvaliacaoForm, PlanejamentoForm
from .models import AlocacaoProfessor, Aula, Avaliacao, FechamentoDiario, Frequencia, Nota, PeriodoLetivo, Planejamento  # noqa: F401


class PainelProfessorView(ProfessorMixin, TemplateView):
    template_name = "diario/painel_professor.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        qs = (AlocacaoProfessor.objects.filter(instituicao=self.instituicao, professor=self.request.user,
                                               situacao=AlocacaoProfessor.Situacao.ATIVA)
              .select_related("turma", "disciplina", "ano_letivo")
              .annotate(n_aulas=Count("aulas", filter=Q(aulas__status="FINALIZADA"), distinct=True),
                        n_pendentes=Count("aulas", filter=~Q(aulas__status="FINALIZADA"), distinct=True),
                        n_avaliacoes=Count("avaliacoes", distinct=True),
                        n_planos=Count("planejamentos", distinct=True)))
        linhas = []
        for a in qs:
            freq = calculos.frequencia_da_alocacao(a)
            media = (sum(v[0] for v in freq.values()) / len(freq)) if freq else None
            linhas.append({"a": a, "alunos": servicos.matriculas_da_turma(a.turma).count(),
                           "freq": round(media, 1) if media is not None else None})
        ctx["linhas"] = linhas
        ctx["total_alunos"] = sum(l["alunos"] for l in linhas)
        return ctx


class _AlocacaoView(ProfessorMixin, TemplateView):
    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["alocacao"] = self.alocacao
        return ctx


class DiarioView(_AlocacaoView):
    template_name = "diario/diario.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        a = self.alocacao
        fech = {f.periodo_id: f for f in FechamentoDiario.objects.filter(alocacao=a)}
        ctx["periodos"] = [{"p": p, "fechado": bool(fech.get(p.pk) and fech[p.pk].fechado)}
                           for p in servicos.periodos_da_alocacao(a)]
        ctx["alunos"] = servicos.matriculas_da_turma(a.turma).count()
        return ctx


class FecharPeriodoView(ProfessorMixin, View):
    def post(self, request, pk, periodo_pk):
        periodo = get_object_or_404(servicos.periodos_da_alocacao(self.alocacao), pk=periodo_pk)
        try:
            servicos.fechar_diario(self.alocacao, periodo, request.user)
            messages.success(request, f"Diário de {periodo.nome} fechado.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("diario:diario", pk=pk)


# ----------------------------------------------------------------------------- aulas e frequência
class AulaListView(_AlocacaoView):
    template_name = "diario/aula_lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["aulas"] = Aula.objects.filter(alocacao=self.alocacao).select_related("periodo")
        return ctx


class AulaFormView(_AlocacaoView):
    template_name = "diario/aula_form.html"

    def get_aula(self):
        pk = self.kwargs.get("aula_pk")
        return get_object_or_404(Aula, pk=pk, alocacao=self.alocacao, instituicao=self.instituicao) if pk else None

    def _linhas(self, aula, post=None):
        atuais = {f.matricula_id: f.situacao for f in aula.frequencias.all()} if aula else {}
        out = []
        for m in servicos.matriculas_da_turma(self.alocacao.turma):
            sit = (post.get(f"freq_{m.pk}") if post is not None else atuais.get(m.pk)) or ""
            out.append({"m": m, "sit": sit})
        return out

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aula = self.get_aula()
        post = kw.get("post")
        ctx.update(aula=aula, form=kw.get("form") or AulaForm(instance=aula), linhas=self._linhas(aula, post),
                   somente_leitura=bool(aula and aula.status == Aula.Status.FINALIZADA))
        return ctx

    def post(self, request, *args, **kwargs):
        aula = self.get_aula()
        if aula and aula.status == Aula.Status.FINALIZADA:
            messages.error(request, "Aula finalizada não pode ser alterada.")
            return redirect("diario:aulas", pk=self.alocacao.pk)
        form = AulaForm(request.POST, instance=aula)
        finalizar = request.POST.get("acao") == "finalizar"
        presencas = {}
        for m in servicos.matriculas_da_turma(self.alocacao.turma):
            v = request.POST.get(f"freq_{m.pk}")
            if v:
                presencas[m.pk] = v
        if form.is_valid():
            try:
                obj = servicos.salvar_aula(alocacao=self.alocacao, aula=aula, dados=form.cleaned_data,
                                           presencas=presencas, finalizar=finalizar, por=request.user)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, "Aula finalizada." if finalizar else "Rascunho salvo.")
                return redirect("diario:aulas", pk=self.alocacao.pk)
        return self.render_to_response(self.get_context_data(form=form, post=request.POST))


class FrequenciaResumoView(_AlocacaoView):
    template_name = "diario/frequencia.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        _, linhas = calculos.desempenho_da_alocacao(self.alocacao)
        ctx["linhas"] = linhas
        ctx["minima"] = servicos.criterios_da_alocacao(self.alocacao)[1]
        ctx["baixa"] = [l for l in linhas if l["baixa_frequencia"]]
        return ctx


# ----------------------------------------------------------------------------- avaliações e notas
class AvaliacaoListView(_AlocacaoView):
    template_name = "diario/avaliacao_lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["avaliacoes"] = (Avaliacao.objects.filter(alocacao=self.alocacao).select_related("periodo")
                             .annotate(lancadas=Count("notas", filter=Q(notas__valor__isnull=False))))
        return ctx


class AvaliacaoCreateView(_AlocacaoView):
    template_name = "generic/form.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        modelo = servicos.modelo_da_alocacao(self.alocacao)
        padrao = modelo.nota_maxima if modelo else servicos.configuracao(self.instituicao).nota_maxima_padrao
        ctx.update(titulo="Nova avaliação", subtitulo=f"{self.alocacao.disciplina} — {self.alocacao.turma}",
                   form=kw.get("form") or AvaliacaoForm(alocacao=self.alocacao, initial={"nota_maxima": padrao}),
                   cancelar_url=reverse("diario:avaliacoes", args=[self.alocacao.pk]))
        return ctx

    def post(self, request, *args, **kwargs):
        form = AvaliacaoForm(request.POST, alocacao=self.alocacao)
        if form.is_valid():
            try:
                servicos.criar_avaliacao(alocacao=self.alocacao, dados=form.cleaned_data, por=request.user)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, "Avaliação cadastrada.")
                return redirect("diario:avaliacoes", pk=self.alocacao.pk)
        return self.render_to_response(self.get_context_data(form=form))


class NotasView(_AlocacaoView):
    template_name = "diario/notas.html"

    def get_avaliacao(self):
        return get_object_or_404(Avaliacao, pk=self.kwargs["av_pk"], alocacao=self.alocacao, instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        av = self.get_avaliacao()
        notas = {n.matricula_id: n.valor for n in av.notas.all()}
        post = kw.get("post")
        linhas = []
        for m in servicos.matriculas_da_turma(self.alocacao.turma):
            v = post.get(f"nota_{m.pk}") if post is not None else notas.get(m.pk)
            linhas.append({"m": m, "valor": "" if v is None else (str(v).replace(",", ".") if post is None else v)})
        ctx.update(av=av, linhas=linhas, fechado=servicos.diario_fechado(self.alocacao, av.periodo))
        return ctx

    def post(self, request, *args, **kwargs):
        av = self.get_avaliacao()
        notas = {}
        try:
            for m in servicos.matriculas_da_turma(self.alocacao.turma):
                bruto = (request.POST.get(f"nota_{m.pk}") or "").strip().replace(",", ".")
                notas[m.pk] = Decimal(bruto) if bruto else None
            servicos.lancar_notas(av, notas, request.user)
        except InvalidOperation:
            messages.error(request, "Informe apenas números nas notas.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            messages.success(request, "Notas salvas.")
            return redirect("diario:avaliacoes", pk=self.alocacao.pk)
        return self.render_to_response(self.get_context_data(post=request.POST))


class DesempenhoView(_AlocacaoView):
    template_name = "diario/desempenho.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        periodos, linhas = calculos.desempenho_da_alocacao(self.alocacao)
        media, freq, modelo = servicos.criterios_da_alocacao(self.alocacao)
        ctx.update(periodos=periodos, linhas=linhas, media_minima=media, freq_minima=freq, modelo=modelo,
                   tem_rec_final=bool(modelo and modelo.usa_rec_final))
        return ctx


# ----------------------------------------------------------------------------- planejamento
class PlanejamentoListView(_AlocacaoView):
    template_name = "diario/planejamento_lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        planos = list(Planejamento.objects.filter(alocacao=self.alocacao).select_related("periodo"))
        aulas = Aula.objects.filter(alocacao=self.alocacao, status=Aula.Status.FINALIZADA).order_by("data")
        ministrado = {}
        for au in aulas:
            ministrado.setdefault(au.periodo_id, []).append(au)
        for p in planos:  # comparação: conteúdos previstos × ministrados (no período, ou no ano para o anual)
            lista = [au for k, v in ministrado.items() for au in v if p.periodo_id is None or k == p.periodo_id]
            p.ministrados = lista
        ctx["planos"] = planos
        return ctx


class PlanejamentoFormView(_AlocacaoView):
    template_name = "generic/form.html"

    def get_plano(self):
        pk = self.kwargs.get("plano_pk")
        return get_object_or_404(Planejamento, pk=pk, alocacao=self.alocacao, instituicao=self.instituicao) if pk else None

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        plano = self.get_plano()
        ctx.update(titulo="Planejamento pedagógico", subtitulo=f"{self.alocacao.disciplina} — {self.alocacao.turma}",
                   form=kw.get("form") or PlanejamentoForm(instance=plano, alocacao=self.alocacao),
                   cancelar_url=reverse("diario:planejamentos", args=[self.alocacao.pk]))
        return ctx

    def post(self, request, *args, **kwargs):
        if self.alocacao.situacao != AlocacaoProfessor.Situacao.ATIVA:
            messages.error(request, "A alocação está encerrada.")
            return redirect("diario:planejamentos", pk=self.alocacao.pk)
        plano = self.get_plano()
        form = PlanejamentoForm(request.POST, instance=plano, alocacao=self.alocacao)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.alocacao, obj.instituicao = self.alocacao, self.instituicao
            obj.save()
            servicos._audit(self.instituicao, request.user, "PLANEJAMENTO_SALVO", "Planejamento", obj.pk, "Planejamento salvo")
            messages.success(request, "Planejamento salvo.")
            return redirect("diario:planejamentos", pk=self.alocacao.pk)
        return self.render_to_response(self.get_context_data(form=form))
