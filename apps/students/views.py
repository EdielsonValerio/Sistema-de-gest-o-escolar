from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView

from apps.academic.models import AnoLetivo, SerieReferencia, Turma
from apps.audit.models import RegistroAuditoria
from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, PaginacaoMixin
from apps.enrollments.models import Matricula

from .forms import AlunoForm, ResponsavelForm
from .models import Aluno, Responsavel
from .selectors import filtrar_por_busca
from .services import aplicar_responsavel_financeiro, cadastrar_aluno

ORDENACOES = {"codigo": "codigo", "nome": "nome", "nascimento": "data_nascimento", "cadastro": "criado_em"}


class AlunoListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Aluno
    permission_required = "students.view_aluno"
    template_name = "students/aluno_lista.html"
    context_object_name = "alunos"

    def get_queryset(self):
        g = self.request.GET
        qs = super().get_queryset()
        qs = filtrar_por_busca(qs, g.get("q"))
        if g.get("situacao") in ("ativo", "inativo"):
            qs = qs.filter(ativo=g["situacao"] == "ativo")

        mat = {}
        if g.get("ano"):
            mat["matriculas__ano_letivo_id"] = g["ano"]
        if g.get("serie"):
            mat["matriculas__turma__serie_ofertada__serie_referencia_id"] = g["serie"]
        if g.get("turma"):
            mat["matriculas__turma_id"] = g["turma"]
        if g.get("situacao_matricula") in Matricula.Status.values:
            mat["matriculas__status"] = g["situacao_matricula"]
        if mat:
            qs = qs.filter(**mat)  # mesma matrícula satisfaz todos os filtros
        if g.get("sem_matricula") == "1":
            qs = qs.exclude(matriculas__status__in=Matricula.STATUS_VIGENTES)

        o = g.get("o", "nome")
        campo = ORDENACOES.get(o.lstrip("-"), "nome")
        qs = qs.order_by(("-" if o.startswith("-") else "") + campo, "id").distinct()
        vigentes = (Matricula.objects.filter(status__in=Matricula.STATUS_VIGENTES)
                    .select_related("turma", "turma__serie_ofertada__serie_referencia", "ano_letivo")
                    .order_by("-ano_letivo__ano", "tipo", "-id"))
        return qs.prefetch_related(Prefetch("matriculas", queryset=vigentes, to_attr="mat_vigentes"))

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        for a in ctx["alunos"]:
            oficiais = [m for m in a.mat_vigentes if m.tipo == "OFICIAL"]
            a.mat_atual = oficiais[0] if oficiais else None
            a.qtd_nao_oficiais = len([m for m in a.mat_vigentes if m.tipo == "NAO_OFICIAL"])
        g = self.request.GET
        ctx.update(
            anos=AnoLetivo.objects.filter(instituicao=self.instituicao),
            series=SerieReferencia.objects.filter(ativa=True),
            turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("-ano_letivo__ano", "nome"),
            status_matricula=Matricula.Status.choices,
            filtros=g, o=g.get("o", "nome"),
            tem_filtro=any(g.get(k) for k in ("q", "situacao", "ano", "serie", "turma", "situacao_matricula",
                                              "sem_matricula")),
        )
        return ctx


class AlunoFormMixin:
    template_name = "students/aluno_form.html"

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["instituicao"] = self.instituicao
        return kw


class AlunoCreateView(AcessoMixin, TemplateView):
    permission_required = "students.add_aluno"
    template_name = "students/aluno_form.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["form"] = kw.get("form") or AlunoForm(instituicao=self.instituicao)
        ctx["titulo"] = "Novo aluno"
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        form = AlunoForm(request.POST, request.FILES, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        aluno = form.save(commit=False)
        aluno.instituicao = self.instituicao
        cadastrar_aluno(aluno, request.user)
        aplicar_responsavel_financeiro(aluno, form.cleaned_data, request.user)
        messages.success(request, f"Aluno cadastrado. Código único: {aluno.codigo}.")
        return redirect("students:aluno_ficha", pk=aluno.pk)


class AlunoUpdateView(AcessoMixin, TemplateView):
    permission_required = "students.change_aluno"
    template_name = "students/aluno_form.html"

    def get_aluno(self):
        return get_object_or_404(Aluno, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aluno = self.get_aluno()
        ctx["aluno"] = aluno
        ctx["form"] = kw.get("form") or AlunoForm(instance=aluno, instituicao=self.instituicao)
        ctx["titulo"] = f"Editar aluno — {aluno.nome}"
        return ctx

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        aluno = self.get_aluno()
        form = AlunoForm(request.POST, request.FILES, instance=aluno, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        campos = [c for c in form.changed_data if not c.startswith("resp_")]
        aluno = form.save()
        aplicar_responsavel_financeiro(aluno, form.cleaned_data, request.user)
        registrar(instituicao=self.instituicao, usuario=request.user, acao="ALUNO_ALTERADO", entidade="Aluno",
                  entidade_id=aluno.pk, aluno_id=aluno.pk, descricao="Cadastro alterado",
                  dados={"campos_alterados": campos})
        messages.success(request, "Cadastro atualizado.")
        return redirect("students:aluno_ficha", pk=aluno.pk)


class AlunoFichaView(InstituicaoQuerysetMixin, DetailView):
    model = Aluno
    permission_required = "students.view_aluno"
    template_name = "students/aluno_ficha.html"
    context_object_name = "aluno"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aluno, user = self.object, self.request.user
        mats = list(aluno.matriculas.select_related("turma", "turma__serie_ofertada__serie_referencia",
                                                    "ano_letivo", "matricula_anterior", "criado_por",
                                                    "matricula_anterior__turma")
                    .order_by("-criado_em", "-id"))
        ctx["matriculas_oficiais"] = [m for m in mats if m.tipo == "OFICIAL"]
        ctx["matriculas_nao_oficiais"] = [m for m in mats if m.tipo == "NAO_OFICIAL"]
        ctx["mostrar_cpf"] = user.has_perm("students.ver_cpf_completo")
        ctx["responsavel_atual"] = aluno.responsavel_financeiro_atual
        ctx["historico_responsaveis"] = aluno.responsaveis_financeiros.select_related("responsavel")

        if user.has_perm("finance.view_parcela"):
            from apps.finance.models import AjusteParcela, Estorno, Parcela, Recebimento
            parcelas = list(aluno.parcelas.select_related("contrato", "responsavel", "matricula")
                            .order_by("vencimento", "numero"))
            ativas = [p for p in parcelas if p.status != "CANCELADA"]
            ctx["parcelas"] = parcelas
            ctx["parcelas_abertas"] = [p for p in ativas if p.status in ("ABERTA", "PARCIAL") and not p.vencida]
            ctx["parcelas_vencidas"] = [p for p in ativas if p.vencida]
            ctx["parcelas_quitadas"] = [p for p in ativas if p.status == "QUITADA"]
            from decimal import Decimal
            ctx["total_contratado"] = sum((p.valor_devido for p in ativas), Decimal("0.00"))
            ctx["total_recebido"] = sum((p.valor_pago for p in ativas), Decimal("0.00"))
            ctx["saldo_devedor"] = ctx["total_contratado"] - ctx["total_recebido"]
            ctx["contratos"] = list({p.contrato_id: p.contrato for p in parcelas}.values())
            ctx["recebimentos"] = aluno.recebimentos.select_related("estorno").order_by("-criado_em")[:50]
            ctx["ajustes"] = (AjusteParcela.objects.filter(parcela__aluno=aluno).select_related("parcela", "usuario")
                              .order_by("-criado_em")[:50])
            ctx["estornos"] = Estorno.objects.filter(recebimento__aluno=aluno).select_related("recebimento")
            ctx["mostrar_financeiro"] = True
            ctx["matriculas_sem_contrato"] = [m for m in ctx["matriculas_oficiais"] + ctx["matriculas_nao_oficiais"]
                                              if m.vigente and not hasattr(m, "contrato")]
        ctx["historico"] = (RegistroAuditoria.objects.filter(instituicao=self.instituicao, aluno_id=aluno.pk)
                            .select_related("usuario")[:100])
        ctx["aba"] = self.request.GET.get("aba", "dados")
        return ctx


class ResponsavelListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Responsavel
    permission_required = "students.view_responsavel"
    template_name = "students/responsavel_lista.html"
    context_object_name = "responsaveis"

    def get_queryset(self):
        qs = super().get_queryset()
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(nome__icontains=q) | Q(documento__icontains=q) | Q(email_cobranca__icontains=q))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["q"] = self.request.GET.get("q", "")
        return ctx


class ResponsavelFormView(AcessoMixin, TemplateView):
    template_name = "generic/form.html"
    editando = False

    def get_obj(self):
        if self.editando:
            return get_object_or_404(Responsavel, pk=self.kwargs["pk"], instituicao=self.instituicao)
        return None

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        obj = self.get_obj()
        ctx.update(form=kw.get("form") or ResponsavelForm(instance=obj, instituicao=self.instituicao),
                   titulo="Editar responsável financeiro" if obj else "Novo responsável financeiro",
                   subtitulo="Cadastro próprio: a mesma pessoa pode responder por vários alunos.",
                   cancelar_url=reverse("students:responsavel_lista"))
        return ctx

    def post(self, request, *args, **kwargs):
        obj = self.get_obj()
        form = ResponsavelForm(request.POST, instance=obj, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        r = form.save()
        registrar(instituicao=self.instituicao, usuario=request.user,
                  acao="RESPONSAVEL_ALTERADO" if obj else "RESPONSAVEL_CRIADO", entidade="Responsavel",
                  entidade_id=r.pk, descricao=f"Responsável {r.pk}", dados={"campos": form.changed_data})
        messages.success(request, "Responsável salvo.")
        return redirect("students:responsavel_lista")


class ResponsavelCreateView(ResponsavelFormView):
    permission_required = "students.add_responsavel"


class ResponsavelUpdateView(ResponsavelFormView):
    permission_required = "students.change_responsavel"
    editando = True


class ResponsavelDetailView(InstituicaoQuerysetMixin, DetailView):
    """Cadastro do responsável financeiro e seu histórico (alunos e cobranças)."""

    model = Responsavel
    permission_required = "students.view_responsavel"
    template_name = "students/responsavel_detalhe.html"
    context_object_name = "responsavel"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        r, user = self.object, self.request.user
        ctx["vinculos"] = r.vinculos_alunos.select_related("aluno").order_by("-inicio")
        if user.has_perm("finance.view_parcela"):
            from decimal import Decimal
            ativas = r.parcelas.exclude(status="CANCELADA")
            ctx["parcelas"] = r.parcelas.select_related("aluno").order_by("vencimento")[:100]
            tot = ativas.aggregate(d=Sum("valor_devido"), p=Sum("valor_pago"))
            ctx["total_devido"] = tot["d"] or Decimal("0.00")
            ctx["total_pago"] = tot["p"] or Decimal("0.00")
            ctx["saldo"] = ctx["total_devido"] - ctx["total_pago"]
            ctx["recebimentos"] = r.recebimentos.select_related("aluno").order_by("-criado_em")[:50]
            ctx["mostrar_financeiro"] = True
        return ctx
