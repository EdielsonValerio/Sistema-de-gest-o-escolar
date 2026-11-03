"""Consulta no banco os boletos pendentes de todas as escolas com cobrança ativa (rede de segurança para avisos perdidos).

Agende para rodar de tempos em tempos (ex.: a cada hora): `python manage.py conciliar_cobrancas`.
"""
from django.core.management.base import BaseCommand

from apps.billing import services
from apps.billing.models import ConfiguracaoBancaria


class Command(BaseCommand):
    help = "Concilia os boletos pendentes com o banco (baixa pagamentos que o aviso automático não trouxe)."

    def handle(self, *args, **opts):
        total = 0
        for cfg in ConfiguracaoBancaria.objects.filter(status=ConfiguracaoBancaria.Status.ATIVA).select_related("instituicao"):
            n = services.conciliar(cfg.instituicao)
            total += n
            self.stdout.write(f"{cfg.instituicao}: {n} pagamento(s) baixado(s)")
        self.stdout.write(self.style.SUCCESS(f"Concluído: {total} pagamento(s) baixado(s) no total."))
