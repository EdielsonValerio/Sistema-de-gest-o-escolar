"""Telas da equipe: ligar o portal da escola e liberar/revogar o acesso de cada responsável."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin
from apps.students.models import Aluno, Responsavel

from . import services as sv
from .models import AcessoAluno, AcessoResponsavel, ConfiguracaoPortal


class ConfiguracaoPortalView(AcessoMixin, TemplateView):
    permission_required = "portal.configurar_portal"
    template_name = "portal/config.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        cfg = ConfiguracaoPortal.obter(self.instituicao)
        base = (self.request.build_absolute_uri("/").rstrip("/"))
        from django.conf import settings
        base = settings.SITE_URL or base
        ctx.update(cfg=cfg, url=f"{base}{reverse('portal:login', args=[cfg.slug])}",
                   total=AcessoResponsavel.objects.filter(instituicao=self.instituicao).count(),
                   liberados=AcessoResponsavel.objects.filter(instituicao=self.instituicao, liberado=True).count())
        return ctx

    def post(self, request, *args, **kwargs):
        cfg = ConfiguracaoPortal.obter(self.instituicao)
        ligar = request.POST.get("acao") == "ativar"
        if request.POST.get("acao") in ("ativar", "desativar"):
            cfg.ativo = ligar
            cfg.save(update_fields=["ativo", "atualizado_em"])
            registrar(instituicao=self.instituicao, usuario=request.user,
                      acao="PORTAL_ATIVADO" if ligar else "PORTAL_DESATIVADO", entidade="ConfiguracaoPortal",
                      entidade_id=cfg.pk, descricao="Portal do responsável " + ("ativado" if ligar else "desativado"))
            messages.success(request, "Portal ativado." if ligar else
                             "Portal desativado: nenhum responsável consegue entrar até reativar.")
        return redirect("portal:config")


class AcessoResponsavelView(AcessoMixin, TemplateView):
    """Ficha de acesso do responsável: liberar, revogar, convite/senha e permissões por aluno."""

    permission_required = ("portal.gerenciar_acesso_portal", "students.view_responsavel")
    template_name = "portal/acesso.html"

    @property
    def responsavel(self):
        return get_object_or_404(Responsavel, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        r = self.responsavel
        acesso = AcessoResponsavel.objects.filter(responsavel=r).first()
        cfg = ConfiguracaoPortal.obter(self.instituicao)
        vinculos = []
        if acesso:
            sv.sincronizar_alunos(acesso)
            vinculos = acesso.alunos.select_related("aluno").order_by("aluno__nome")
        ctx.update(responsavel=r, acesso=acesso, cfg=cfg, vinculos=vinculos,
                   cpf_ok=len("".join(c for c in r.documento if c.isdigit())) == 11,
                   convite_pendente=bool(acesso and acesso.convite_expira and acesso.convite_expira > timezone.now()))
        return ctx

    def _auditar(self, acao, acesso, texto):
        registrar(instituicao=self.instituicao, usuario=self.request.user, acao=acao, entidade="AcessoResponsavel",
                  entidade_id=acesso.pk, descricao=texto)

    def post(self, request, *args, **kwargs):
        r = self.responsavel
        acao = request.POST.get("acao")
        volta = redirect("portal:acesso", pk=r.pk)
        acesso = AcessoResponsavel.objects.filter(responsavel=r).first()
        try:
            if acao == "criar" and acesso is None:
                acesso = sv.criar_acesso(r, request.user)
                self._auditar("PORTAL_ACESSO_CRIADO", acesso, f"Acesso ao portal criado para {r.nome}")
                messages.success(request, "Acesso criado. Ajuste as permissões dos alunos, libere e envie o convite.")
            elif acesso is None:
                return volta
            elif acao == "liberar":
                acesso.liberado, acesso.revogado_em = True, None
                acesso.save(update_fields=["liberado", "revogado_em"])
                self._auditar("PORTAL_ACESSO_LIBERADO", acesso, f"Acesso ao portal liberado para {r.nome}")
                messages.success(request, "Acesso liberado.")
            elif acao == "revogar":
                acesso.liberado, acesso.revogado_em = False, timezone.now()
                acesso.convite_hash, acesso.convite_expira = "", None
                acesso.save(update_fields=["liberado", "revogado_em", "convite_hash", "convite_expira"])
                self._auditar("PORTAL_ACESSO_REVOGADO", acesso, f"Acesso ao portal REVOGADO para {r.nome}")
                messages.success(request, "Acesso revogado: o responsável é desconectado imediatamente.")
            elif acao in ("convite", "redefinir"):
                if not acesso.liberado:
                    raise ValidationError("Libere o acesso antes de enviar o convite.")
                if acao == "redefinir":  # apaga a senha: sessões abertas caem e o responsável cria outra
                    acesso.senha_hash = ""
                    acesso.save(update_fields=["senha_hash"])
                url, enviado = sv.enviar_convite(acesso, request)
                self._auditar("PORTAL_CONVITE_ENVIADO" if acao == "convite" else "PORTAL_SENHA_REDEFINIDA", acesso,
                              ("Convite" if acao == "convite" else "Redefinição de senha") + f" para {r.nome}")
                if enviado:
                    messages.success(request, f"Convite enviado para {r.email_cobranca}. Vale por 3 dias e só pode ser usado uma vez.")
                else:
                    messages.warning(request, "E-mail não enviado (sem e-mail cadastrado ou envio não configurado). "
                                              f"Copie o link e envie ao responsável (aparece só agora): {url}")
            elif acao == "alunos":
                for v in acesso.alunos.all():
                    v.financeiro = request.POST.get(f"fin_{v.pk}") == "on"
                    v.pedagogico = request.POST.get(f"ped_{v.pk}") == "on"
                    v.save(update_fields=["financeiro", "pedagogico"])
                self._auditar("PORTAL_PERMISSOES_ALTERADAS", acesso, f"Permissões por aluno alteradas ({r.nome})")
                messages.success(request, "Permissões salvas.")
            elif acao == "adicionar":
                aluno = Aluno.objects.filter(instituicao=self.instituicao,
                                             codigo=(request.POST.get("codigo") or "").strip()).first()
                if aluno is None:
                    raise ValidationError("Aluno não encontrado pelo código.")
                AcessoAluno.objects.get_or_create(acesso=acesso, aluno=aluno, instituicao=self.instituicao,
                                                  defaults={"financeiro": False, "pedagogico": False})
                self._auditar("PORTAL_ALUNO_ADICIONADO", acesso, f"Aluno {aluno.codigo} incluído no acesso de {r.nome}")
                messages.success(request, "Aluno incluído. Marque o que o responsável pode ver e salve.")
            elif acao == "remover":
                v = acesso.alunos.filter(pk=request.POST.get("vinculo") if str(request.POST.get("vinculo", "")).isdigit() else 0).first()
                if v:
                    v.delete()
                    self._auditar("PORTAL_ALUNO_REMOVIDO", acesso, f"Aluno removido do acesso de {r.nome}")
                    messages.success(request, "Aluno removido do acesso.")
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
        return volta
