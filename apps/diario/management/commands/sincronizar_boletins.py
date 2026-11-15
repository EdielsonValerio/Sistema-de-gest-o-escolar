from django.core.management.base import BaseCommand

from apps.diario.boletim import sincronizar_alocacao
from apps.diario.models import AlocacaoProfessor


class Command(BaseCommand):
    help = ("Recalcula as notas liberadas ao boletim (e os resultados finais) de todas as alocações com diário "
            "fechado. Útil após atualizar o sistema ou corrigir dados; é seguro repetir.")

    def handle(self, *args, **opts):
        n = 0
        for a in AlocacaoProfessor.objects.filter(fechamentos__fechado=True).distinct().select_related(
                "instituicao", "turma", "disciplina", "ano_letivo"):
            sincronizar_alocacao(a)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"{n} alocação(ões) sincronizada(s)."))
