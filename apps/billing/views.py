import hmac
import json

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, ListView, TemplateView

from apps.core.mixins import AcessoMixin, InstituicaoQuerysetMixin, PaginacaoMixin
from apps.finance.models import Parcela

from . import crypto, services
from .forms import BoletoTesteForm, ConfiguracaoBancariaForm
from .models import Cobranca, ConfiguracaoBancaria, Provedor
from .providers import ErroProvedor
from .providers.simulado import Simulado


def _erro(request, exc):
    messages.error(request, exc.messages[0] if isinstance(exc, ValidationError) else str(exc))


# --------------------------------------------------------------------------- tela única de configuração
class ConfiguracaoCobrancaView(AcessoMixin, TemplateView):
    """Configuração financeira da escola: 1) dados do banco, 2) boleto de teste, 3) confirmar recebimento, 4) ativar."""

    template_name = "billing/configuracao.html"
    permission_required = "billing.configurar_cobranca"

    def _cfg(self):
        return services.configuracao(self.instituicao)

    def _webhook_url(self, cfg):
        caminho = reverse("billing:webhook", args=[cfg.webhook_slug])
        return (settings.SITE_URL + caminho) if settings.SITE_URL else self.request.build_absolute_uri(caminho)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cfg = self._cfg()
        try:
            token, chave_mascarada = cfg.webhook_token, crypto.mascarar(cfg.api_key)
            segredo_ok = True
        except crypto.SegredoIlegivel:
            token, chave_mascarada, segredo_ok = "", "", False
        ativa = cfg.status == ConfiguracaoBancaria.Status.ATIVA
        ctx.update(
            cfg=cfg, segredo_ok=segredo_ok, chave_mascarada=chave_mascarada, webhook_token=token,
            webhook_url=self._webhook_url(cfg), etapas=services.etapas(cfg), ativa=ativa,
            simulado=cfg.provedor == Provedor.SIMULADO,
            form=kw.get("form") or ConfiguracaoBancariaForm(initial={
                "provedor": cfg.provedor, "ambiente": cfg.ambiente, "banco_nome": cfg.banco_nome,
                "agencia": cfg.agencia, "conta": cfg.conta, "beneficiario_nome": cfg.beneficiario_nome or self.instituicao.nome,
                "beneficiario_documento": cfg.beneficiario_documento or "".join(c for c in self.instituicao.cnpj if c.isdigit()),
                "instrucoes": cfg.instrucoes}, cfg=cfg, instituicao=self.instituicao),
            form_teste=kw.get("form_teste") or BoletoTesteForm(instituicao=self.instituicao),
            testes=Cobranca.objects.filter(instituicao=self.instituicao, eh_teste=True)[:5],
            sem_boleto=services.pendentes_sem_boleto(self.instituicao).count() if ativa else 0,
            com_erro=Cobranca.objects.filter(instituicao=self.instituicao, status=Cobranca.Status.ERRO).count(),
            pode_gerar=self.request.user.has_perm("billing.gerar_boleto"))
        return ctx

    def post(self, request, *args, **kwargs):
        acao, cfg = request.POST.get("acao", ""), self._cfg()
        manipulador = getattr(self, f"_acao_{acao}", None)
        if manipulador is None:
            raise Http404
        try:
            resposta = manipulador(request, cfg)
        except (ValidationError, ErroProvedor, crypto.SegredoIlegivel) as exc:
            _erro(request, exc)
            resposta = None
        return resposta or redirect("billing:configuracao")

    # ---- ações (cada botão da tela)
    def _acao_salvar(self, request, cfg):
        form = ConfiguracaoBancariaForm(request.POST, cfg=cfg, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        _, reaberta = services.salvar_configuracao(self.instituicao, form.cleaned_data, request.user)
        messages.success(request, "Dados salvos." + (" Como o banco/ambiente/chave mudou, é preciso gerar e confirmar "
                                                      "um novo boleto de teste antes de continuar cobrando." if reaberta else ""))

    def _acao_conexao(self, request, cfg):
        services.testar_conexao(cfg)
        messages.success(request, "Conexão com o banco confirmada: a chave de API e o ambiente estão corretos.")

    def _acao_teste(self, request, cfg):
        form = BoletoTesteForm(request.POST, instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form_teste=form))
        d = form.cleaned_data
        services.gerar_boleto_teste(cfg, request.user, pagador_nome=d["pagador_nome"],
                                    pagador_documento=d["pagador_documento"], pagador_email=d["pagador_email"],
                                    valor=d["valor"])
        messages.success(request, "Boleto de teste gerado. Pague-o e depois confirme o recebimento na etapa 3.")

    def _acao_consultar(self, request, cfg):
        cob = get_object_or_404(Cobranca, pk=request.POST.get("cobranca"), instituicao=self.instituicao, eh_teste=True)
        cob = services.consultar_e_baixar(cob, request.user)
        if cob.status == Cobranca.Status.PAGA:
            messages.success(request, "Recebimento do boleto de teste confirmado. Agora você já pode ativar a cobrança.")
        else:
            messages.info(request, "O banco ainda não informou o pagamento deste boleto de teste.")

    def _acao_simular(self, request, cfg):
        if cfg.provedor != Provedor.SIMULADO:
            raise Http404
        cob = get_object_or_404(Cobranca, pk=request.POST.get("cobranca"), instituicao=self.instituicao)
        Simulado.simular_pagamento(cob.id_externo)
        services.consultar_e_baixar(cob, request.user)
        messages.success(request, "Pagamento simulado e confirmado.")

    def _acao_ativar(self, request, cfg):
        services.ativar_cobranca(cfg, request.user)
        messages.success(request, "Cobrança com registro bancário ativada. As próximas parcelas já nascem com boleto.")

    def _acao_suspender(self, request, cfg):
        services.suspender_cobranca(cfg, request.user)
        messages.warning(request, "Cobrança suspensa: novas parcelas não gerarão boleto até reativar.")

    def _acao_novo_token(self, request, cfg):
        services.novo_token_webhook(self.instituicao, request.user)
        messages.warning(request, "Token renovado. Atualize-o também no painel do banco, senão os avisos de pagamento serão recusados.")

    def _exigir_gerar(self, request):
        if not request.user.has_perm("billing.gerar_boleto"):
            raise Http404

    def _acao_lote(self, request, cfg):
        self._exigir_gerar(request)
        r = services.gerar_em_lote(self.instituicao, request.user, incluir_erros=bool(request.POST.get("incluir_erros")))
        messages.success(request, f"{r['ok']} boleto(s) registrado(s), {r['erro']} com erro."
                                  + (f" Ainda restam {r['restantes']} parcela(s): clique de novo para continuar." if r["restantes"] else ""))

    def _acao_conciliar(self, request, cfg):
        n = services.conciliar(self.instituicao, request.user)
        messages.success(request, f"Conciliação concluída: {n} pagamento(s) novo(s) baixado(s).")


# --------------------------------------------------------------------------- lista e boleto
class CobrancaListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = Cobranca
    template_name = "billing/cobranca_lista.html"
    permission_required = "billing.view_cobranca"
    context_object_name = "cobrancas"

    def get_queryset(self):
        qs = (super().get_queryset().filter(eh_teste=False)
              .select_related("parcela", "parcela__aluno", "recebimento"))
        g = self.request.GET
        if g.get("situacao") in Cobranca.Status.values:
            qs = qs.filter(status=g["situacao"])
        if g.get("divergencia"):
            qs = qs.exclude(divergencia="")
        q = (g.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(parcela__aluno__nome__icontains=q) | Q(parcela__aluno__codigo__icontains=q)
                           | Q(nosso_numero__icontains=q) | Q(pagador_nome__icontains=q))
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cfg = services.configuracao(self.instituicao)
        ctx.update(situacoes=Cobranca.Status.choices, filtros=self.request.GET, cfg=cfg,
                   ativa=services.cobranca_ativa(self.instituicao) is not None,
                   pode_gerar=self.request.user.has_perm("billing.gerar_boleto"),
                   pode_cancelar=self.request.user.has_perm("billing.cancelar_boleto"))
        return ctx


class BoletoView(InstituicaoQuerysetMixin, DetailView):
    """Boleto para imprimir: dados, linha digitável, código de barras e QR Code Pix."""

    model = Cobranca
    template_name = "billing/boleto.html"
    permission_required = "billing.view_cobranca"
    context_object_name = "cob"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["cfg"] = services.configuracao(self.instituicao)
        return ctx


class _AcaoPost(AcessoMixin, View):
    """Ação simples por POST sobre uma cobrança da escola, seguida de volta à lista."""

    def post(self, request, pk):
        cob = get_object_or_404(Cobranca, pk=pk, instituicao=self.instituicao)
        try:
            self.executar(request, cob)
        except (ValidationError, ErroProvedor) as exc:
            _erro(request, exc)
        destino = request.POST.get("next", "")
        return redirect(destino) if destino.startswith("/") and not destino.startswith("//") \
            else redirect("billing:cobranca_lista")


class CobrancaConsultarView(_AcaoPost):
    permission_required = "billing.view_cobranca"

    def executar(self, request, cob):
        cob = services.consultar_e_baixar(cob, request.user)
        messages.success(request, f"Situação no banco: {cob.get_status_display().lower()}.")


class CobrancaCancelarView(_AcaoPost):
    permission_required = "billing.cancelar_boleto"

    def executar(self, request, cob):
        services.cancelar_cobranca(cob, request.user, motivo=(request.POST.get("motivo") or "").strip())
        messages.success(request, "Boleto cancelado.")


class CobrancaGerarParcelaView(AcessoMixin, View):
    permission_required = "billing.gerar_boleto"

    def post(self, request, pk):
        parcela = get_object_or_404(Parcela, pk=pk, instituicao=self.instituicao)
        try:
            cob = services.registrar_cobranca(parcela, request.user)
            if cob.status == Cobranca.Status.ERRO:
                messages.error(request, f"Boleto não registrado: {cob.erro}")
            else:
                messages.success(request, "Boleto registrado no banco.")
        except ValidationError as exc:
            _erro(request, exc)
        destino = request.POST.get("next", "")
        return redirect(destino) if destino.startswith("/") and not destino.startswith("//") \
            else redirect("finance:parcela_detalhe", pk=pk)


# --------------------------------------------------------------------------- aviso do banco (webhook)
@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """Recebe os avisos do banco. Autenticado pelo token compartilhado (cabeçalho `asaas-access-token`)."""

    http_method_names = ["post"]
    LIMITE_BYTES = 256 * 1024

    def post(self, request, slug):
        cfg = ConfiguracaoBancaria.objects.select_related("instituicao").filter(webhook_slug=slug).first()
        if cfg is None or cfg.provedor != Provedor.ASAAS:
            return HttpResponse(status=404)
        try:
            token = cfg.webhook_token
        except crypto.SegredoIlegivel:
            return HttpResponse(status=500)
        if not token or not hmac.compare_digest(request.headers.get("asaas-access-token", "").encode(), token.encode()):
            return HttpResponse(status=401)
        if len(request.body) > self.LIMITE_BYTES:
            return HttpResponse(status=413)
        try:
            payload = json.loads(request.body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError:
            return HttpResponse(status=400)
        try:
            resultado = services.processar_webhook(cfg, payload)
        except ValidationError:
            return HttpResponse(status=400)
        except ErroProvedor:
            return HttpResponse(status=500)  # o banco tenta de novo mais tarde
        return JsonResponse({"ok": True, "resultado": resultado})
