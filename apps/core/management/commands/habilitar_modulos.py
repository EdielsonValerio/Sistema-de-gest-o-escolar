from django.core.management.base import BaseCommand, CommandError

from apps.audit.services import registrar
from apps.core import modulos
from apps.core.models import Instituicao


class Command(BaseCommand):
    help = ("Define os módulos contratados. Exemplos:\n"
            "  habilitar_modulos --todas-escolas --todos          (libera tudo para todas as escolas)\n"
            "  habilitar_modulos --escola 3 --modulos diario,financeiro\n"
            "  habilitar_modulos --escola 3 --modulos portal --acrescentar\n"
            "  habilitar_modulos --listar")

    def add_arguments(self, p):
        p.add_argument("--escola", type=int, help="ID da escola")
        p.add_argument("--todas-escolas", action="store_true")
        p.add_argument("--modulos", help="códigos separados por vírgula: " + ", ".join(modulos.TODOS))
        p.add_argument("--todos", action="store_true", help="todos os módulos")
        p.add_argument("--acrescentar", action="store_true", help="soma aos já habilitados em vez de substituir")
        p.add_argument("--listar", action="store_true")

    def handle(self, *a, **o):
        if o["listar"]:
            for e in Instituicao.objects.order_by("pk"):
                self.stdout.write(f"{e.pk}\t{e.nome}\t{','.join(e.modulos or []) or '(só a base)'}")
            return
        if bool(o["escola"]) == bool(o["todas_escolas"]):
            raise CommandError("Informe --escola ID ou --todas-escolas.")
        if bool(o["modulos"]) == bool(o["todos"]):
            raise CommandError("Informe --modulos a,b,c ou --todos.")
        novos = list(modulos.TODOS) if o["todos"] else [c.strip() for c in o["modulos"].split(",") if c.strip()]
        invalidos = [c for c in novos if c not in modulos.MODULOS]
        if invalidos:
            raise CommandError(f"Módulo(s) desconhecido(s): {', '.join(invalidos)}")
        escolas = Instituicao.objects.all() if o["todas_escolas"] else Instituicao.objects.filter(pk=o["escola"])
        if not escolas.exists():
            raise CommandError("Escola não encontrada.")
        for e in escolas:
            antes = list(e.modulos or [])
            final = modulos.normalizar(set(antes) | set(novos) if o["acrescentar"] else novos)
            faltam = modulos.dependencias_faltando(final)
            if faltam:
                raise CommandError(f"Escola {e.pk}: {faltam[0][0]} exige {faltam[0][1]}.")
            if final != antes:
                e.modulos = final
                e.save(update_fields=["modulos"])
                registrar(instituicao=e, usuario=None, acao="MODULOS_ALTERADOS", entidade="Instituicao", entidade_id=e.pk,
                          descricao="Módulos alterados por linha de comando", dados={"antes": antes, "depois": final})
            self.stdout.write(f"{e.pk} {e.nome}: {','.join(final) or '(só a base)'}")
