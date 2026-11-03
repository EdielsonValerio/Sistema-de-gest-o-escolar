"""Notificações de contas a pagar vencidas ou a vencer (sino na barra superior)."""
from django.utils import timezone

from . import services

LIMITE_ITENS = 8


def notificacoes_contas_pagar(request):
    user = getattr(request, "user", None)
    escola = getattr(request, "instituicao", None)
    if user is None or not user.is_authenticated or escola is None or not user.has_perm("payables.view_contapagar"):
        return {}
    contas = services.contas_a_vencer(escola)
    total = contas.count()
    if total == 0:
        return {"notif_contas": {"total": 0, "itens": [], "vencidas": 0, "mais": 0}}
    itens = list(contas[:LIMITE_ITENS])
    return {"notif_contas": {"total": total, "itens": itens, "mais": total - len(itens),
                             "vencidas": contas.filter(vencimento__lt=timezone.localdate()).count()}}
