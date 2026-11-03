"""Visibilidade do menu do assistente de IA (só aparece para escolas com o módulo habilitado)."""
from .models import ConfiguracaoIA


def assistente(request):
    user = getattr(request, "user", None)
    escola = getattr(request, "instituicao", None)
    if user is None or not user.is_authenticated or escola is None:
        return {}
    cfg = ConfiguracaoIA.objects.filter(instituicao=escola, habilitada=True).first()
    if cfg is None:
        return {}
    return {"ia_menu": {"usar": cfg.pronta and user.has_perm("assistente.usar_assistente"),
                        "configurar": user.has_perm("assistente.configurar_ia")}}
