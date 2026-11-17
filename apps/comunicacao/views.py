from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import TemplateView, View

from apps.audit.services import registrar
from apps.billing.crypto import SegredoIlegivel, cifrar, decifrar, mascarar
from apps.core.mixins import AcessoMixin

from . import services as sv
from . import whatsapp as wa
from .forms import ComunicadoForm, ConfiguracaoForm
from .models import Comunicado, ConfiguracaoWhatsApp, EnvioComunicado


class ConfiguracaoView(AcessoMixin, TemplateView):
    permission_required = "comunicacao.configurar_whatsapp"
    template_name = "comunicacao/config.html"

    def cfg(self):
        return ConfiguracaoWhatsApp.obter(self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cfg = self.cfg()
        try:
            mask = mascarar(decifrar(cfg.token_cifrado))
        except SegredoIlegivel:
            mask = "ilegível: informe o token de novo"
        ctx.update(cfg=cfg, form=kw.get("form") or ConfiguracaoForm(instance=cfg), token_mascarado=mask)
        return ctx

    def post(self, request, *args, **kwargs):
        cfg = self.cfg()
        acao = request.POST.get("acao")
        try:
            if acao == "salvar":
                form = ConfiguracaoForm(request.POST, instance=cfg)
                if not form.is_valid():
                    return self.render_to_response(self.get_context_data(form=form))
                obj = form.save(commit=False)
                if form.cleaned_data["token"]:
                    obj.token_cifrado = cifrar(form.cleaned_data["token"])
                obj.ativo = False  # qualquer mudança exige novo teste e nova ativação
                obj.testada_em = None
                obj.save()
                registrar(instituicao=self.instituicao, usuario=request.user, acao="WHATSAPP_CONFIGURADO",
                          entidade="ConfiguracaoWhatsApp", entidade_id=cfg.pk, descricao="Configuração do WhatsApp salva")
                messages.success(request, "Configuração salva. Teste a conexão e ative o envio.")
            elif acao == "testar":
                if not cfg.completa:
                    raise ValidationError("Preencha ID do número, token e modelo antes de testar.")
                fone, nome = wa.testar_conexao(cfg, decifrar(cfg.token_cifrado))
                cfg.testada_em = timezone.now()
                cfg.save(update_fields=["testada_em"])
                messages.success(request, f"Conexão ok: {nome or 'número'} {fone}.")
            elif acao == "ativar":
                if not (cfg.completa and cfg.testada_em):
                    raise ValidationError("Salve a configuração completa e teste a conexão antes de ativar.")
                cfg.ativo = True
                cfg.save(update_fields=["ativo"])
                registrar(instituicao=self.instituicao, usuario=request.user, acao="WHATSAPP_ATIVADO",
                          entidade="ConfiguracaoWhatsApp", entidade_id=cfg.pk, descricao="Envio por WhatsApp ativado")
                messages.success(request, "Envio por WhatsApp ativado.")
            elif acao == "desativar":
                cfg.ativo = False
                cfg.save(update_fields=["ativo"])
                registrar(instituicao=self.instituicao, usuario=request.user, acao="WHATSAPP_DESATIVADO",
                          entidade="ConfiguracaoWhatsApp", entidade_id=cfg.pk, descricao="Envio por WhatsApp desativado")
                messages.success(request, "Envio desativado.")
            elif acao == "teste_envio":
                if not cfg.completa:
                    raise ValidationError("Configure antes de enviar um teste.")
                fone = wa.normalizar_telefone(request.POST.get("telefone"))
                if not fone:
                    raise ValidationError("Informe um celular com DDD.")
                wa.enviar_modelo(cfg, decifrar(cfg.token_cifrado), fone, "Mensagem de teste do sistema da escola.")
                messages.success(request, f"Mensagem de teste enviada para {wa.mascarar_telefone(fone)}.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        except (wa.ErroWhatsApp, SegredoIlegivel) as e:
            messages.error(request, str(e))
        return redirect("comunicacao:config")


class ListaView(AcessoMixin, TemplateView):
    permission_required = "comunicacao.enviar_comunicado"
    template_name = "comunicacao/lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cs = list(Comunicado.objects.filter(instituicao=self.instituicao).select_related("criado_por")[:50])
        for c in cs:
            c.res = c.resumo()
        ctx.update(comunicados=cs, cfg=ConfiguracaoWhatsApp.obter(self.instituicao))
        return ctx


class NovoView(AcessoMixin, View):
    permission_required = "comunicacao.enviar_comunicado"

    def get(self, request):
        return render(request, "comunicacao/novo.html", {"form": ComunicadoForm(instituicao=self.instituicao),
                                                         "cfg": ConfiguracaoWhatsApp.obter(self.instituicao)})

    def post(self, request):
        form = ComunicadoForm(request.POST, instituicao=self.instituicao)
        cfg = ConfiguracaoWhatsApp.obter(self.instituicao)
        if not form.is_valid():
            return render(request, "comunicacao/novo.html", {"form": form, "cfg": cfg})
        d = form.cleaned_data
        kw = {"responsavel": d.get("responsavel"), "turmas": list(d.get("turmas") or [])}
        if request.POST.get("confirmar") != "1":
            linhas = sv.previa(self.instituicao, d["alvo"], **kw)
            return render(request, "comunicacao/previa.html", {
                "form": form, "linhas": linhas, "validos": sum(1 for _, e in linhas if e),
                "sem": sum(1 for _, e in linhas if not e), "mensagem": wa.limpar_parametro(d["mensagem"]), "cfg": cfg})
        excluidos = {int(x) for x in request.POST.getlist("excluir") if x.isdigit()}
        try:
            c = sv.criar_comunicado(instituicao=self.instituicao, usuario=request.user, alvo=d["alvo"],
                                    mensagem=d["mensagem"], excluidos=excluidos, **kw)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return render(request, "comunicacao/novo.html", {"form": form, "cfg": cfg})
        return redirect("comunicacao:detalhe", pk=c.pk)


class DetalheView(AcessoMixin, TemplateView):
    permission_required = "comunicacao.enviar_comunicado"
    template_name = "comunicacao/detalhe.html"

    @property
    def comunicado(self):
        return get_object_or_404(Comunicado, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        c = self.comunicado
        ctx.update(c=c, res=c.resumo(), envios=c.envios.select_related("responsavel"))
        return ctx

    def post(self, request, *args, **kwargs):
        c = self.comunicado
        try:
            n = sv.processar(c)
            registrar(instituicao=self.instituicao, usuario=request.user, acao="COMUNICADO_ENVIADO", entidade="Comunicado",
                      entidade_id=c.pk, descricao=f"Lote de envio: {n} tentativa(s)")
            messages.success(request, f"{n} envio(s) processado(s).")
        except (ValidationError, SegredoIlegivel) as e:
            messages.error(request, " ".join(e.messages) if hasattr(e, "messages") else str(e))
        return redirect("comunicacao:detalhe", pk=c.pk)
