from uuid import uuid4

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, PaginacaoMixin

from . import services
from .forms import ContaPagarForm, JustificativaForm, LiquidarForm
from .models import Categoria, ContaPagar

SITUACOES = [("abertas", "Em aberto"), ("vencidas", "Vencidas"), ("a_vencer", "A vencer (prazo de aviso)"),
             ("liquidadas", "Liquidadas"), ("desativadas", "Desativadas"), ("todas", "Todas")]


def _data(txt):
    from datetime import date
    try:
        return date.fromisoformat(txt) if txt else None
    except ValueError:
        return None


class ContaListView(PaginacaoMixin, AcessoMixin, ListView):
    permission_required = "payables.view_contapagar"
    template_name = "payables/conta_lista.html"
    context_object_name = "contas"

    def filtrar(self):
        g, hoje = self.request.GET, timezone.localdate()
        qs = ContaPagar.objects.filter(instituicao=self.instituicao)
        sit = g.get("situacao") if g.get("situacao") in dict(SITUACOES) else "abertas"
        S = ContaPagar.Status
        if sit == "abertas":
            qs = qs.filter(status=S.ABERTA)
        elif sit == "vencidas":
            qs = qs.filter(status=S.ABERTA, vencimento__lt=hoje)
        elif sit == "a_vencer":
            qs = services.contas_a_vencer(self.instituicao, hoje).filter(vencimento__gte=hoje)
        elif sit == "liquidadas":
            qs = qs.filter(status=S.LIQUIDADA)
        elif sit == "desativadas":
            qs = qs.filter(status=S.DESATIVADA)
        if g.get("categoria") in Categoria.values:
            qs = qs.filter(categoria=g["categoria"])
        if g.get("q"):
            q = g["q"].strip()
            qs = qs.filter(Q(descricao__icontains=q) | Q(prestador__icontains=q) | Q(numero__icontains=q)
                           | Q(numero_documento__icontains=q))
        de, ate = _data(g.get("de")), _data(g.get("ate"))
        if de:
            qs = qs.filter(vencimento__gte=de)
        if ate:
            qs = qs.filter(vencimento__lte=ate)
        ordem = "-vencimento" if sit in ("liquidadas", "desativadas", "todas") else "vencimento"
        return qs.order_by(ordem, "-id" if ordem.startswith("-") else "id"), sit

    def get_queryset(self):
        qs, self.sit = self.filtrar()
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(
            resumo=services.resumo(self.instituicao), situacoes=SITUACOES, sit=self.sit,
            categorias=Categoria.choices,
            total_filtro=self.object_list.aggregate(s=Sum("valor"))["s"] or 0,
            filtros={k: g.get(k, "") for k in ("q", "categoria", "de", "ate")},
            tem_filtro=any(g.get(k) for k in ("q", "categoria", "de", "ate")) or g.get("situacao") not in (None, "abertas"))
        return ctx


class ContaCreateView(AcessoMixin, TemplateView):
    permission_required = "payables.add_contapagar"
    template_name = "generic/form.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        form = kw.get("form") or ContaPagarForm(initial={"token": uuid4(), "repetir": 1},
                                                instituicao=self.instituicao)
        s = services.saldo(self.instituicao)
        ctx.update(form=form, titulo="Nova conta a pagar", cancelar_url=reverse("payables:conta_lista"),
                   subtitulo=f"Saldo disponível hoje: {services.brl(s['saldo'])}. O saldo só é debitado quando "
                             "a conta é liquidada.")
        return ctx

    def post(self, request, *args, **kwargs):
        form = ContaPagarForm(request.POST, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        d = form.cleaned_data
        try:
            contas = services.criar_contas(instituicao=self.instituicao, usuario=request.user, dados=d,
                                           repeticoes=d["repetir"], token=d.get("token"))
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.render_to_response(self.get_context_data(form=form))
        if len(contas) == 1:
            messages.success(request, f"Conta {contas[0].numero} cadastrada.")
            return redirect("payables:conta_detalhe", pk=contas[0].pk)
        messages.success(request, f"{len(contas)} contas mensais cadastradas ({contas[0].numero} a {contas[-1].numero}).")
        return redirect("payables:conta_lista")


class _ContaMixin:
    @cached_property
    def conta(self):
        return get_object_or_404(ContaPagar, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def detalhe(self):
        return redirect("payables:conta_detalhe", pk=self.conta.pk)


class ContaUpdateView(_ContaMixin, AcessoMixin, TemplateView):
    permission_required = "payables.change_contapagar"
    template_name = "generic/form.html"

    def dispatch(self, request, *args, **kwargs):
        resp = super().dispatch(request, *args, **kwargs)
        return resp

    def get(self, request, *args, **kwargs):
        if self.conta.status != ContaPagar.Status.ABERTA:
            messages.error(request, "Somente contas em aberto podem ser editadas.")
            return self.detalhe()
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(form=kw.get("form") or ContaPagarForm(instance=self.conta, instituicao=self.instituicao),
                   titulo=f"Editar conta {self.conta.numero}", cancelar_url=reverse("payables:conta_detalhe", args=[self.conta.pk]),
                   subtitulo="Somente contas em aberto podem ser alteradas. Alterações ficam registradas na auditoria.")
        return ctx

    def post(self, request, *args, **kwargs):
        form = ContaPagarForm(request.POST, instance=ContaPagar.objects.get(pk=self.conta.pk) if self.conta.status == "ABERTA" else self.conta,
                              instituicao=self.instituicao)
        if self.conta.status != ContaPagar.Status.ABERTA:
            messages.error(request, "Somente contas em aberto podem ser editadas.")
            return self.detalhe()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            services.editar_conta(self.conta, form.cleaned_data, request.user)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, "Conta atualizada.")
        return self.detalhe()


class ContaDetailView(InstituicaoQuerysetMixin, DetailView):
    model = ContaPagar
    permission_required = "payables.view_contapagar"
    template_name = "payables/conta_detalhe.html"
    context_object_name = "conta"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        c, u = self.object, self.request.user
        S = ContaPagar.Status
        s = services.saldo(self.instituicao)
        cfg = services.configuracao(self.instituicao)
        ctx.update(
            liquidacoes=c.liquidacoes.select_related("usuario"), saldo=s, permite_negativo=cfg.permitir_saldo_negativo,
            ficaria_negativo=c.status == S.ABERTA and s["saldo"] - c.valor < 0,
            pode_editar=u.has_perm("payables.change_contapagar") and c.status == S.ABERTA,
            pode_liquidar=u.has_perm("payables.liquidar_conta") and c.status == S.ABERTA,
            pode_desativar=u.has_perm("payables.desativar_conta") and c.status == S.ABERTA,
            pode_reativar=u.has_perm("payables.desativar_conta") and c.status == S.DESATIVADA,
            pode_estornar=u.has_perm("payables.estornar_liquidacao") and c.status == S.LIQUIDADA,
            outras_do_grupo=(ContaPagar.objects.filter(instituicao=self.instituicao, grupo=c.grupo).exclude(pk=c.pk)
                             .order_by("parcela_numero") if c.grupo else []))
        return ctx


class _AcaoView(_ContaMixin, AcessoMixin, TemplateView):
    """Tela de confirmação de uma ação sobre a conta (liquidar, desativar, estornar)."""

    template_name = "payables/conta_acao.html"
    titulo = botao = ""
    botao_classe = "btn-primary"
    subtitulo = ""
    exige_status = ContaPagar.Status.ABERTA
    mensagem_status = "Esta operação não está disponível para a situação atual da conta."
    mensagem_sucesso = "Operação concluída."

    def get_form(self, data=None):
        raise NotImplementedError

    def executar(self, form):
        raise NotImplementedError

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(form=kw.get("form") or self.get_form(), conta=self.conta, saldo=services.saldo(self.instituicao),
                   permite_negativo=services.configuracao(self.instituicao).permitir_saldo_negativo,
                   titulo=f"{self.titulo} — {self.conta.numero}", subtitulo=self.subtitulo, botao=self.botao,
                   botao_classe=self.botao_classe, cancelar_url=reverse("payables:conta_detalhe", args=[self.conta.pk]))
        return ctx

    def get(self, request, *args, **kwargs):
        if self.conta.status != self.exige_status:
            messages.error(request, self.mensagem_status)
            return self.detalhe()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        try:
            self.executar(form)
        except ValidationError as exc:
            if self.conta.status != self.exige_status or "já foi" in " ".join(exc.messages):
                messages.error(request, " ".join(exc.messages))
                return self.detalhe()
            form.add_error(None, exc)
            return self.render_to_response(self.get_context_data(form=form))
        messages.success(request, self.mensagem_sucesso)
        return self.detalhe()


class ContaLiquidarView(_AcaoView):
    permission_required = "payables.liquidar_conta"
    titulo, botao, botao_classe = "Liquidar conta", "Confirmar liquidação", "btn-success"
    subtitulo = "O valor liquidado será debitado do saldo total recebido."
    mensagem_status = "Somente contas em aberto podem ser liquidadas."
    mensagem_sucesso = "Conta liquidada e valor debitado do saldo."

    def get_form(self, data=None):
        return LiquidarForm(data, conta=self.conta, instituicao=self.instituicao)

    def executar(self, form):
        d = form.cleaned_data
        services.liquidar_conta(self.conta, usuario=self.request.user, data=d["data"], forma=d["forma"],
                                valor=d["valor"], referencia=d["referencia"], observacao=d["observacao"])


class ContaDesativarView(_AcaoView):
    permission_required = "payables.desativar_conta"
    titulo, botao, botao_classe = "Desativar conta", "Desativar conta", "btn-danger"
    subtitulo = "A conta deixa de ser cobrada e some das notificações. O registro é preservado e pode ser reativado."
    mensagem_status = "Somente contas em aberto podem ser desativadas."
    mensagem_sucesso = "Conta desativada."

    def get_form(self, data=None):
        f = JustificativaForm(data, instituicao=self.instituicao)
        f.fields["justificativa"].label = "Motivo da desativação"
        return f

    def executar(self, form):
        services.desativar_conta(self.conta, self.request.user, form.cleaned_data["justificativa"])


class ContaEstornarLiquidacaoView(_AcaoView):
    permission_required = "payables.estornar_liquidacao"
    exige_status = ContaPagar.Status.LIQUIDADA
    titulo, botao, botao_classe = "Estornar liquidação", "Estornar liquidação", "btn-danger"
    subtitulo = "Use para corrigir um lançamento errado: o valor volta ao saldo e a conta é reaberta. O histórico é preservado."
    mensagem_status = "Somente contas liquidadas podem ter a liquidação estornada."
    mensagem_sucesso = "Liquidação estornada. O valor voltou ao saldo e a conta está em aberto."

    def get_form(self, data=None):
        return JustificativaForm(data, instituicao=self.instituicao)

    def executar(self, form):
        services.estornar_liquidacao(self.conta, self.request.user, form.cleaned_data["justificativa"])


class ContaReativarView(_ContaMixin, AcessoMixin, View):
    permission_required = "payables.desativar_conta"

    def post(self, request, pk):
        try:
            services.reativar_conta(self.conta, request.user)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, "Conta reativada.")
        return self.detalhe()
