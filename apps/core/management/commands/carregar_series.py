from django.core.management.base import BaseCommand

from apps.academic.services import carregar_series_padrao


class Command(BaseCommand):
    help = "Carrega a tabela de referência de séries (códigos internos; idempotente)."

    def handle(self, *args, **opts):
        n = carregar_series_padrao()
        self.stdout.write(self.style.SUCCESS(f"{n} série(s) de referência criada(s)."))
