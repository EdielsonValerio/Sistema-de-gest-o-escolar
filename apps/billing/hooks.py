"""Ganchos chamados pelo módulo financeiro. NUNCA podem quebrar uma operação financeira: tudo roda depois do
commit e qualquer falha é registrada em log (a parcela fica sem boleto e pode ser reenviada pela tela de cobranças).
"""
import logging

from django.db import transaction

log = logging.getLogger(__name__)


def _seguro(funcao, *args):
    try:
        funcao(*args)
    except Exception as exc:  # noqa: BLE001
        log.warning("Cobrança bancária: %s falhou (%s)", funcao.__name__, type(exc).__name__)


def _ativa(instituicao):
    from . import services

    return services.cobranca_ativa(instituicao) is not None


def parcelas_geradas(contrato, usuario):
    """Parcelas novas de um contrato: registra o boleto de cada uma (se a cobrança estiver ativa)."""
    from . import services

    if not _ativa(contrato.instituicao):
        return
    ids = list(contrato.parcelas.values_list("pk", flat=True))

    def registrar():
        from apps.finance.models import Parcela

        for p in Parcela.objects.filter(pk__in=ids).order_by("numero"):
            _seguro(services.registrar_cobranca, p, usuario)

    transaction.on_commit(registrar)


def parcela_alterada(parcela, usuario):
    """Desconto, vencimento, valor ou cancelamento: mantém o boleto pendente igual à parcela."""
    from . import services
    from .models import Cobranca

    if Cobranca.objects.filter(parcela=parcela, status=Cobranca.Status.PENDENTE).exists():
        transaction.on_commit(lambda: _seguro(services.sincronizar_parcela, parcela, usuario))


def recebimento_registrado(parcelas, usuario):
    """Parcela recebida no caixa (ou parcialmente): o boleto pendente é cancelado/atualizado no banco."""
    from . import services
    from .models import Cobranca

    for p in parcelas:
        if Cobranca.objects.filter(parcela=p, status=Cobranca.Status.PENDENTE).exists():
            transaction.on_commit(lambda p=p: _seguro(services.sincronizar_parcela, p, usuario))
