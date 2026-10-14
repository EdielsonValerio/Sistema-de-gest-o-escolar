import uuid
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, DecimalField, F, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.academic.models import Turma
from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, ObjetoFormView, PaginacaoMixin
from apps.students.models import Aluno, Responsavel
from apps.students.selectors import filtrar_por_busca

from . import services
from .forms import AjusteParcelaForm, CaixaForm, PlanoForm
from .models import AjusteParcela, Estorno, Parcela, PlanoFinanceiro, Recebimento

ZERO = Decimal("0.00")
DEC = DecimalField(max_digits=14, decimal_places=2)


def _data(valor):
    from datetime import date
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


# ============================================================================ planos
class PlanoListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = PlanoFinanceiro
    permission_required = "finance.view_planofinanceiro"
    template_name = "finance/plano_lista.html"

    def get_queryset(self):
        g = self.request.GET
        qs = (super().get_queryset().select_related("turma", "turma__ano_letivo")
              .annotate(qtd_contratos=Count("contratos"))
              .order_by("turma__nome", "nome", "id"))
        if g.get("q"):
            qs = qs.filter(Q(nome__icontains=g["q"]) | Q(turma__nome__icontains=g["q"]))
        if g.get("turma"):
            qs = qs.filter(turma_id=g["turma"])
        if g.get("situacao") in PlanoFinanceiro.Situacao.values:
            qs = qs.filter(situacao=g["situacao"])
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(filtros=g, turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("nome"),
                   tem_filtro=any(g.get(k) for k in ("q", "turma", "situacao")))
        return ctx


class PlanoFormView(ObjetoFormView):
    model = PlanoFinanceiro
    form_class = PlanoForm
    template_name = "finance/plano_form.html"
    sucesso_url_name = "finance:plano_lista"
    titulo_novo, titulo_editar = "Novo plano financeiro", "Editar plano financeiro"
    subtitulo = "Alterações valem apenas para novas contratações: parcelas já geradas não são modificadas."
    mensagem_sucesso = "Plano salvo."

    def get_form(self, data=None, files=None):
        form = super().get_form(data, files)
        if data is None and not self.objeto and self.request.GET.get("turma"):
            form.fields["turma"].initial = self.request.GET["turma"]
        return form

    def get_extra_context(self):
        return {"contratos": self.objeto.contratos.count() if self.objeto else 0}

    def apos_salvar(self, obj, form, novo):
        registrar(instituicao=self.instituicao, usuario=self.request.user,
                  acao="PLANO_CRIADO" if novo else "PLANO_ALTERADO", entidade="PlanoFinanceiro", entidade_id=obj.pk,
                  descricao=obj.nome, dados={"turma_id": obj.turma_id, "valor_total": obj.valor_total,
                                             "parcelas": obj.quantidade_parcelas,
                                             "campos_alterados": [] if novo else form.changed_data})


class PlanoCreateView(PlanoFormView):
    permission_required = "finance.add_planofinanceiro"


class PlanoUpdateView(PlanoFormView):
    permission_required = "finance.change_planofinanceiro"
    editando = True


class PlanoDetailView(InstituicaoQuerysetMixin, DetailView):
    model = PlanoFinanceiro
    permission_required = "finance.view_planofinanceiro"
    template_name = "finance/plano_detalhe.html"
    context_object_name = "plano"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["cronograma"] = services.cronograma_do_plano(self.object)
        ctx["qtd_contratos"] = self.object.contratos.count()
        return ctx


# ============================================================================ contas a receber
def _saldo():
    return Sum(F("valor_devido") - F("valor_pago"), output_field=DEC)


class ParcelaListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Parcela
    permission_required = "finance.view_parcela"
    template_name = "finance/parcela_lista.html"
    SITUACOES = [("pendentes", "Em aberto (inclui parciais)"), ("vencidas", "Vencidas"), ("a_vencer", "A vencer"),
                 ("parciais", "Parcialmente pagas"), ("quitadas", "Quitadas"), ("canceladas", "Canceladas"),
                 ("todas", "Todas")]

    def filtrar(self):
        g = self.request.GET
        qs = super().get_queryset().select_related("aluno", "responsavel", "contrato", "matricula__turma")
        sit = g.get("situacao", "pendentes")
        if sit == "pendentes":
            qs = qs.pendentes()
        elif sit == "vencidas":
            qs = qs.vencidas()
        elif sit == "a_vencer":
            qs = qs.a_vencer()
        elif sit == "parciais":
            qs = qs.filter(status="PARCIAL")
        elif sit == "quitadas":
            qs = qs.filter(status="QUITADA")
        elif sit == "canceladas":
            qs = qs.filter(status="CANCELADA")
        if g.get("q"):
            qs = qs.filter(Q(aluno__nome__icontains=g["q"]) | Q(aluno__codigo__icontains=g["q"])
                           | Q(responsavel__nome__icontains=g["q"]))
        for param, campo in (("aluno", "aluno_id"), ("responsavel", "responsavel_id"),
                             ("turma", "matricula__turma_id")):
            if g.get(param, "").isdigit():
                qs = qs.filter(**{campo: g[param]})
        if _data(g.get("de")):
            qs = qs.filter(vencimento__gte=_data(g["de"]))
        if _data(g.get("ate")):
            qs = qs.filter(vencimento__lte=_data(g["ate"]))
        return qs

    def get_queryset(self):
        return self.filtrar().order_by("vencimento", "aluno__nome", "numero")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        base = self.filtrar().exclude(status="CANCELADA")
        tot = base.aggregate(devido=Sum("valor_devido"), pago=Sum("valor_pago"))
        ctx.update(
            filtros=g, situacoes=self.SITUACOES, sit=g.get("situacao", "pendentes"),
            turmas=Turma.objects.filter(instituicao=self.instituicao).order_by("nome"),
            total_devido=tot["devido"] or ZERO, total_pago=tot["pago"] or ZERO,
            total_saldo=(tot["devido"] or ZERO) - (tot["pago"] or ZERO),
            tem_filtro=any(g.get(k) for k in ("q", "aluno", "responsavel", "turma", "de", "ate"))
            or g.get("situacao", "pendentes") != "pendentes")
        return ctx


class ParcelaDetailView(InstituicaoQuerysetMixin, DetailView):
    model = Parcela
    permission_required = "finance.view_parcela"
    template_name = "finance/parcela_detalhe.html"
    context_object_name = "parcela"

    def get_queryset(self):
        return super().get_queryset().select_related("aluno", "responsavel", "contrato", "matricula__turma")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        p, u = self.object, self.request.user
        ctx["ajustes"] = p.ajustes.select_related("usuario")
        ctx["alocacoes"] = p.alocacoes.select_related("recebimento").order_by("-recebimento__criado_em")
        ctx["encargos_hoje"] = services.calcular_encargos(p)
        ctx["editavel"] = p.status in ("ABERTA", "PARCIAL")
        ctx["pode"] = {a: u.has_perm(f"finance.{perm}") for a, perm in (
            ("desconto", "aplicar_desconto"), ("vencimento", "alterar_vencimento"), ("valor", "corrigir_valor"),
            ("cancelar", "cancelar_parcela"))}
        return ctx


class ParcelaAjustarView(AcessoMixin, View):
    PERMS = {"desconto": "finance.aplicar_desconto", "vencimento": "finance.alterar_vencimento",
             "valor": "finance.corrigir_valor", "cancelar": "finance.cancelar_parcela"}
    permission_required = "finance.view_parcela"

    def post(self, request, pk):
        parcela = get_object_or_404(Parcela, pk=pk, instituicao=self.instituicao)
        form = AjusteParcelaForm(request.POST, instituicao=self.instituicao)
        destino = redirect("finance:parcela_detalhe", pk=parcela.pk)
        if not form.is_valid():
            for erros in form.errors.values():
                for e in erros:
                    messages.error(request, e)
            return destino
        d = form.cleaned_data
        if not request.user.has_perm(self.PERMS[d["acao"]]):
            raise PermissionDenied
        try:
            if d["acao"] == "desconto":
                services.aplicar_desconto(parcela, d["valor"], request.user, d["justificativa"])
            elif d["acao"] == "vencimento":
                services.alterar_vencimento(parcela, d["data"], request.user, d["justificativa"])
            elif d["acao"] == "valor":
                services.corrigir_valor(parcela, d["valor"], request.user, d["justificativa"])
            else:
                services.cancelar_parcela(parcela, request.user, d["justificativa"])
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
        else:
            messages.success(request, "Alteração registrada no histórico da parcela.")
        return destino


# ============================================================================ caixa
class CaixaBuscaView(AcessoMixin, TemplateView):
    permission_required = "finance.registrar_recebimento"
    template_name = "finance/caixa_busca.html"

    def get(self, request, *args, **kwargs):
        q = request.GET.get("q", "").strip()
        self.resultados = None
        if q:
            qs = filtrar_por_busca(Aluno.objects.filter(instituicao=self.instituicao), q).order_by("nome")[:30]
            self.resultados = list(qs)
            if len(self.resultados) == 1:
                return redirect("finance:caixa_aluno", pk=self.resultados[0].pk)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(q=self.request.GET.get("q", ""), resultados=self.resultados)
        return ctx


class CaixaAlunoView(AcessoMixin, TemplateView):
    permission_required = "finance.registrar_recebimento"
    template_name = "finance/caixa_aluno.html"

    def get_aluno(self):
        return get_object_or_404(Aluno, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def parcelas_pendentes(self, aluno):
        lista = list(Parcela.objects.filter(aluno=aluno, instituicao=self.instituicao).pendentes()
                     .select_related("responsavel", "matricula__turma").order_by("vencimento", "numero"))
        for p in lista:
            p.acrescimo_hoje = services.acrescimo_atual(p)
            p.devido_hoje = p.valor_original - p.desconto + p.acrescimo_hoje
            p.saldo_hoje = p.devido_hoje - p.valor_pago
        return lista

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        aluno = self.get_aluno()
        parcelas = kw.get("parcelas") or self.parcelas_pendentes(aluno)
        vencidas = [p for p in parcelas if p.vencida]
        ctx.update(
            aluno=aluno, parcelas=parcelas, responsavel=aluno.responsavel_financeiro_atual,
            matriculas=aluno.matriculas.filter(status__in=("PENDENTE", "ATIVA")).select_related("turma"),
            saldo_devedor=sum((p.saldo_hoje for p in parcelas), ZERO),
            saldo_vencido=sum((p.saldo_hoje for p in vencidas), ZERO), qtd_vencidas=len(vencidas),
            qtd_parciais=len([p for p in parcelas if p.status == "PARCIAL"]),
            form=kw.get("form") or CaixaForm(instituicao=self.instituicao, initial={"token": uuid.uuid4()}),
            formas=Recebimento.Forma.choices)
        return ctx

    def post(self, request, *args, **kwargs):
        aluno = self.get_aluno()
        form = CaixaForm(request.POST, instituicao=self.instituicao)
        ids = request.POST.getlist("parcelas")
        itens = [(i, request.POST.get(f"valor_{i}", "")) for i in ids if i.isdigit()]
        if not form.is_valid():
            for erros in form.errors.values():
                for e in erros:
                    messages.error(request, e)
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        try:
            rec, criado = services.registrar_recebimento(
                aluno=aluno, itens=[(int(i), v) for i, v in itens], forma=d["forma"],
                valor_recebido=d["valor_recebido"], usuario=request.user, token=d["token"],
                referencia=d["referencia"])
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
            return self.render_to_response(self.get_context_data(form=CaixaForm(instituicao=self.instituicao,
                                                                              initial={"token": uuid.uuid4()})))
        if criado:
            messages.success(request, f"Recebimento {rec.numero} registrado.")
        else:
            messages.info(request, "Este recebimento já havia sido registrado (solicitação repetida ignorada).")
        return redirect("finance:recebimento_detalhe", pk=rec.pk)


class RecebimentoDetailView(InstituicaoQuerysetMixin, DetailView):
    """Comprovante de recebimento (imprimível)."""

    model = Recebimento
    permission_required = "finance.view_recebimento"
    template_name = "finance/recibo.html"
    context_object_name = "rec"

    def get_queryset(self):
        return super().get_queryset().select_related("aluno", "responsavel")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["alocacoes"] = self.object.alocacoes.select_related("parcela").order_by("parcela__vencimento")
        ctx["estorno"] = Estorno.objects.filter(recebimento=self.object).first()
        ctx["pode_estornar"] = (self.request.user.has_perm("finance.estornar_recebimento")
                                and self.object.status == "CONFIRMADO")
        return ctx


class RecebimentoEstornarView(AcessoMixin, View):
    permission_required = "finance.estornar_recebimento"

    def post(self, request, pk):
        rec = get_object_or_404(Recebimento, pk=pk, instituicao=self.instituicao)
        try:
            services.estornar_recebimento(rec, request.user, request.POST.get("justificativa", ""))
        except ValidationError as exc:
            for m in exc.messages:
                messages.error(request, m)
        else:
            messages.success(request, "Recebimento estornado. O registro original foi preservado.")
        return redirect("finance:recebimento_detalhe", pk=rec.pk)


# ============================================================================ relatórios
class RelatorioRecebimentosView(PaginacaoMixin, AcessoMixin, ListView):
    permission_required = "finance.ver_relatorios"
    template_name = "finance/relatorio_recebimentos.html"

    def filtrar(self):
        g = self.request.GET
        qs = Recebimento.objects.filter(instituicao=self.instituicao).select_related("aluno")
        de, ate = _data(g.get("de")), _data(g.get("ate"))
        if de:
            qs = qs.filter(criado_em__date__gte=de)
        if ate:
            qs = qs.filter(criado_em__date__lte=ate)
        if g.get("forma") in Recebimento.Forma.values:
            qs = qs.filter(forma=g["forma"])
        if g.get("operador", "").isdigit():
            qs = qs.filter(operador_id=g["operador"])
        if g.get("q"):
            qs = qs.filter(Q(aluno__nome__icontains=g["q"]) | Q(aluno__codigo__icontains=g["q"])
                           | Q(numero__icontains=g["q"]))
        if g.get("status") in Recebimento.Status.values:
            qs = qs.filter(status=g["status"])
        return qs

    def get_queryset(self):
        return self.filtrar().order_by("-criado_em", "-id")

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        confirmados = self.filtrar().filter(status="CONFIRMADO")
        por_forma = list(confirmados.values("forma").annotate(total=Sum("valor_total"), qtd=Count("id"))
                         .order_by("forma"))
        for r in por_forma:
            r["rotulo"] = Recebimento.Forma(r["forma"]).label
        por_operador = list(confirmados.values("operador_nome").annotate(total=Sum("valor_total"), qtd=Count("id"))
                            .order_by("operador_nome"))
        g = self.request.GET
        ctx.update(
            filtros=g, formas=Recebimento.Forma.choices, status_opcoes=Recebimento.Status.choices,
            operadores=User.objects.filter(perfil__instituicao=self.instituicao).order_by("username"),
            por_forma=por_forma, por_operador=por_operador,
            total_geral=confirmados.aggregate(t=Sum("valor_total"))["t"] or ZERO,
            tem_filtro=any(g.get(k) for k in ("de", "ate", "forma", "operador", "q", "status")))
        return ctx


class RelatorioEstornosView(PaginacaoMixin, AcessoMixin, ListView):
    permission_required = "finance.ver_relatorios"
    template_name = "finance/relatorio_estornos.html"

    def get_queryset(self):
        g = self.request.GET
        qs = (Estorno.objects.filter(recebimento__instituicao=self.instituicao)
              .select_related("recebimento", "recebimento__aluno").order_by("-criado_em"))
        de, ate = _data(g.get("de")), _data(g.get("ate"))
        if de:
            qs = qs.filter(criado_em__date__gte=de)
        if ate:
            qs = qs.filter(criado_em__date__lte=ate)
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(filtros=g, tem_filtro=bool(g.get("de") or g.get("ate")),
                   total=self.get_queryset().aggregate(t=Sum("recebimento__valor_total"))["t"] or ZERO)
        return ctx
