from django.core.management.base import BaseCommand

from apps.comunicacao import services as sv
from apps.comunicacao.models import Comunicado, EnvioComunicado


class Command(BaseCommand):
    help = "Envia os comunicados que ainda têm destinatários na fila (rode a cada minuto no agendador do servidor)."

    def handle(self, *a, **o):
        total = 0
        ids = (EnvioComunicado.objects.filter(status=EnvioComunicado.Status.PENDENTE)
               .values_list("comunicado_id", flat=True).distinct())
        for c in Comunicado.objects.filter(pk__in=list(ids)).select_related("instituicao"):
            try:
                total += sv.processar(c)
            except Exception as e:  # noqa: BLE001 — uma escola com problema não para as outras
                self.stderr.write(f"Comunicado {c.pk}: {e}")
        self.stdout.write(f"{total} envio(s) processado(s).")
