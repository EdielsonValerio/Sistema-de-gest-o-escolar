from django.db.models import Count, Q
from django.urls import reverse
from django.views.generic import DetailView, ListView

from apps.audit.services import registrar
from apps.core.mixins import InstituicaoQuerysetMixin, ObjetoFormView, PaginacaoMixin, AcessoMixin

from .forms import AnoLetivoForm, SerieOfertadaForm, SerieReferenciaForm, TurmaForm
from .models import AnoLetivo, SerieOfertada, SerieReferencia, Turma

VIGENTES = ("PENDENTE", "ATIVA")


def _audit(view, obj, novo, form, entidade):
    registrar(instituicao=view.instituicao, usuario=view.request.user,
              acao=f"{entidade.upper()}_{'CRIADO' if novo else 'ALTERADO'}", entidade=entidade, entidade_id=obj.pk,
              descricao=str(obj)[:200], dados={"campos": [] if novo else form.changed_data})


# ----------------------------------------------------------------------------- anos letivos
class AnoLetivoListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = AnoLetivo
    permission_required = "academic.view_anoletivo"
    template_name = "academic/ano_lista.html"

    def get_queryset(self):
        return super().get_queryset().annotate(qtd_series=Count("series_ofertadas", distinct=True),
                                               qtd_turmas=Count("turmas", distinct=True))


class AnoLetivoFormView(ObjetoFormView):
    model = AnoLetivo
    form_class = AnoLetivoForm
    sucesso_url_name = "academic:ano_lista"
    titulo_novo, titulo_editar = "Novo ano letivo", "Editar ano letivo"
    subtitulo = "Anos letivos encerrados continuam consultáveis: alunos, matrículas e cobranças são preservados."
    mensagem_sucesso = "Ano letivo salvo."

    def apos_salvar(self, obj, form, novo):
        _audit(self, obj, novo, form, "AnoLetivo")


class AnoLetivoCreateView(AnoLetivoFormView):
    permission_required = "academic.add_anoletivo"


class AnoLetivoUpdateView(AnoLetivoFormView):
    permission_required = "academic.change_anoletivo"
    editando = True


# ----------------------------------------------------------------------------- séries
class SerieOfertadaListView(PaginacaoMixin, AcessoMixin, ListView):
    model = SerieOfertada
    permission_required = "academic.view_serieofertada"
    template_name = "academic/serie_lista.html"

    def get_queryset(self):
        qs = (SerieOfertada.objects.filter(ano_letivo__instituicao=self.instituicao)
              .select_related("ano_letivo", "serie_referencia")
              .annotate(qtd_turmas=Count("turmas", distinct=True))
              .order_by("-ano_letivo__ano", "serie_referencia__etapa", "serie_referencia__ordem"))
        if self.request.GET.get("ano"):
            qs = qs.filter(ano_letivo_id=self.request.GET["ano"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["anos"] = AnoLetivo.objects.filter(instituicao=self.instituicao)
        ctx["filtros"] = self.request.GET
        return ctx


class SerieOfertadaFormView(ObjetoFormView):
    model = SerieOfertada
    form_class = SerieOfertadaForm
    escopo = "ano_letivo__instituicao"
    sucesso_url_name = "academic:serie_lista"
    titulo_novo, titulo_editar = "Nova série ofertada", "Editar série ofertada"
    subtitulo = "Oferta de uma série do cadastro de referência em um ano letivo."
    mensagem_sucesso = "Série ofertada salva."

    def apos_salvar(self, obj, form, novo):
        _audit(self, obj, novo, form, "SerieOfertada")


class SerieOfertadaCreateView(SerieOfertadaFormView):
    permission_required = "academic.add_serieofertada"


class SerieOfertadaUpdateView(SerieOfertadaFormView):
    permission_required = "academic.change_serieofertada"
    editando = True


class SerieReferenciaListView(AcessoMixin, ListView):
    model = SerieReferencia
    permission_required = "academic.view_seriereferencia"
    template_name = "academic/serie_ref_lista.html"


class SerieReferenciaFormView(ObjetoFormView):
    model = SerieReferencia
    form_class = SerieReferenciaForm
    escopo = None  # tabela de referência compartilhada
    sucesso_url_name = "academic:serie_ref_lista"
    titulo_novo, titulo_editar = "Nova série de referência", "Editar série de referência"
    subtitulo = "Estrutura educacional usada pela instituição. Os códigos são internos, não oficiais do MEC."
    mensagem_sucesso = "Série de referência salva."

    def apos_salvar(self, obj, form, novo):
        _audit(self, obj, novo, form, "SerieReferencia")


class SerieReferenciaCreateView(SerieReferenciaFormView):
    permission_required = "academic.add_seriereferencia"


class SerieReferenciaUpdateView(SerieReferenciaFormView):
    permission_required = "academic.change_seriereferencia"
    editando = True


# ----------------------------------------------------------------------------- turmas
class TurmaListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Turma
    permission_required = "academic.view_turma"
    template_name = "academic/turma_lista.html"

    def get_queryset(self):
        g = self.request.GET
        qs = (super().get_queryset().select_related("ano_letivo", "serie_ofertada__serie_referencia")
              .annotate(ocupacao=Count("matriculas", filter=Q(matriculas__status__in=VIGENTES), distinct=True))
              .order_by("-ano_letivo__ano", "-ano_referencia", "nome", "id"))
        if g.get("q"):
            qs = qs.filter(Q(nome__icontains=g["q"]) | Q(codigo__icontains=g["q"]))
        if g.get("tipo") in Turma.Tipo.values:
            qs = qs.filter(tipo=g["tipo"])
        if g.get("situacao") in Turma.Situacao.values:
            qs = qs.filter(situacao=g["situacao"])
        if g.get("ano"):
            qs = qs.filter(Q(ano_letivo_id=g["ano"]))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(anos=AnoLetivo.objects.filter(instituicao=self.instituicao), filtros=g,
                   tipos=Turma.Tipo.choices, situacoes=Turma.Situacao.choices,
                   tem_filtro=any(g.get(k) for k in ("q", "tipo", "situacao", "ano")))
        return ctx


class TurmaDetailView(InstituicaoQuerysetMixin, DetailView):
    model = Turma
    permission_required = "academic.view_turma"
    template_name = "academic/turma_detalhe.html"
    context_object_name = "turma"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        t = self.object
        ctx["matriculas"] = t.matriculas.select_related("aluno").order_by("aluno__nome")
        ctx["ocupacao"] = t.matriculas.filter(status__in=VIGENTES).count()
        if self.request.user.has_perm("finance.view_planofinanceiro"):
            ctx["planos"] = t.planos_financeiros.all()
        return ctx


class TurmaFormView(ObjetoFormView):
    model = Turma
    form_class = TurmaForm
    template_name = "academic/turma_form.html"
    sucesso_url_name = "academic:turma_lista"
    titulo_novo, titulo_editar = "Nova turma", "Editar turma"
    subtitulo = "Turma oficial (série ofertada em um ano letivo) ou não oficial (curso com período próprio)."
    mensagem_sucesso = "Turma salva."

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        from django.contrib import messages
        from django.db import transaction
        from django.shortcuts import redirect
        novo = self.objeto is None
        with transaction.atomic():
            obj = form.save(commit=False)
            if novo:
                from .services import gerar_codigo_turma
                obj.codigo = gerar_codigo_turma(self.instituicao)
            obj.save()
            _audit(self, obj, novo, form, "Turma")
        messages.success(request, self.mensagem_sucesso)
        return redirect("academic:turma_detalhe", pk=obj.pk)


class TurmaCreateView(TurmaFormView):
    permission_required = "academic.add_turma"


class TurmaUpdateView(TurmaFormView):
    permission_required = "academic.change_turma"
    editando = True
