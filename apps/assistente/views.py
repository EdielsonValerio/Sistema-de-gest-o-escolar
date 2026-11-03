import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from apps.billing import crypto
from apps.core.mixins import AcessoMixin

from . import ferramentas, servicos
from .cliente import ErroIA
from .models import Conversa, Mensagem

MAX_CONVERSAS_LISTADAS = 30


class _IAHabilitadaMixin(AcessoMixin):
    """Escola sem o módulo habilitado pelo sistema não enxerga nada (404 lógico = 403)."""

    def has_permission(self):
        if not super().has_permission():
            return False
        if not servicos.configuracao(self.instituicao).habilitada:
            raise PermissionDenied
        return True


class ConfiguracaoView(_IAHabilitadaMixin, TemplateView):
    template_name = "assistente/configuracao.html"
    permission_required = "assistente.configurar_ia"

    def _form(self, cfg, data=None):
        from .forms import IntegracaoForm
        return IntegracaoForm(data, cfg=cfg, instituicao=self.instituicao, initial={
            "modelo": cfg.modelo, "limite_mensal_tokens": cfg.limite_mensal_tokens})

    def get_context_data(self, **kw):
        cfg = servicos.configuracao(self.instituicao)
        ctx = super().get_context_data(**kw)
        try:
            mascara = crypto.mascarar(cfg.api_key) if cfg.tem_api_key else ""
            legivel = True
        except crypto.SegredoIlegivel:
            mascara, legivel = "", False
        ctx.update(cfg=cfg, form=kw.get("form") or self._form(cfg), chave_mascarada=mascara, segredo_ok=legivel,
                   uso=servicos.uso_do_mes(self.instituicao))
        return ctx

    def post(self, request, *args, **kwargs):
        cfg = servicos.configuracao(self.instituicao)
        acao = request.POST.get("acao")
        try:
            if acao == "salvar":
                form = self._form(cfg, request.POST)
                if not form.is_valid():
                    return self.render_to_response(self.get_context_data(form=form))
                d = form.cleaned_data
                servicos.salvar_integracao(cfg, api_key=d["api_key"], modelo=d["modelo"],
                                           limite=d["limite_mensal_tokens"], aceitar_termo=d["aceitar_termo"],
                                           usuario=request.user)
                messages.success(request, "Integração salva. Agora teste a conexão.")
            elif acao == "testar":
                servicos.testar_conexao(cfg)
                messages.success(request, "Conexão com a IA funcionando. Você já pode ativar o assistente.")
            elif acao in ("ativar", "desativar"):
                servicos.ativar(cfg, acao == "ativar", request.user)
                messages.success(request, "Assistente de IA " + ("ativado." if acao == "ativar" else "desativado."))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        except ErroIA as exc:
            messages.error(request, str(exc))
        except crypto.SegredoIlegivel as exc:
            messages.error(request, str(exc))
        return redirect("assistente:configuracao")


class _UsoMixin(_IAHabilitadaMixin):
    permission_required = "assistente.usar_assistente"

    def has_permission(self):
        if not super().has_permission():
            return False
        if not servicos.configuracao(self.instituicao).pronta:
            raise PermissionDenied
        return True

    def conversas(self):
        return Conversa.objects.filter(instituicao=self.instituicao, usuario=self.request.user)


def _serializar(m):
    return {"papel": m.papel, "texto": m.texto, "grafico": m.grafico, "aviso": m.aviso}


class ChatView(_UsoMixin, TemplateView):
    template_name = "assistente/chat.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        lista = self.conversas()
        atual = None
        pk = self.request.GET.get("c")
        if pk and pk.isdigit():
            atual = get_object_or_404(lista, pk=pk)
        msgs = [_serializar(m) for m in atual.mensagens.all()] if atual else []
        cfg = servicos.configuracao(self.instituicao)
        uso = servicos.uso_do_mes(self.instituicao)
        ctx.update(conversas=lista[:MAX_CONVERSAS_LISTADAS], atual=atual, mensagens=msgs,
                   tem_ferramentas=bool(ferramentas.disponiveis(self.request.user)),
                   limite_atingido=bool(cfg.limite_mensal_tokens and uso and uso.tokens >= cfg.limite_mensal_tokens))
        return ctx


class PerguntarView(_UsoMixin, View):
    def post(self, request):
        try:
            corpo = json.loads(request.body.decode() or "{}")
        except ValueError:
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        pergunta = corpo.get("pergunta", "")
        if not isinstance(pergunta, str):
            return JsonResponse({"erro": "Requisição inválida."}, status=400)
        cid = corpo.get("conversa")
        if cid:
            conversa = get_object_or_404(self.conversas(), pk=cid)
        else:
            conversa = Conversa.objects.create(instituicao=self.instituicao, usuario=request.user)
        try:
            msg = servicos.responder(conversa=conversa, usuario=request.user, pergunta=pergunta)
        except ValidationError as exc:
            return JsonResponse({"erro": exc.messages[0], "conversa": conversa.pk}, status=400)
        except ErroIA as exc:
            return JsonResponse({"erro": str(exc), "conversa": conversa.pk}, status=502)
        except crypto.SegredoIlegivel as exc:
            return JsonResponse({"erro": str(exc), "conversa": conversa.pk}, status=400)
        return JsonResponse({"conversa": conversa.pk, "titulo": conversa.titulo, **_serializar(msg)})


class ExcluirConversaView(_UsoMixin, View):
    def post(self, request, pk):
        get_object_or_404(self.conversas(), pk=pk).delete()
        messages.success(request, "Conversa excluída.")
        return redirect("assistente:chat")
