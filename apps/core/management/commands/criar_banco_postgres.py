"""Cria o usuário e o banco PostgreSQL configurados no .env (se ainda não existirem)."""
from getpass import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Cria o usuário (DB_USER) e o banco (DB_NAME) do PostgreSQL instalado nesta máquina, usando as "
            "credenciais de um administrador do PostgreSQL (normalmente o usuário 'postgres'). "
            "Depois rode: python manage.py migrate")
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--admin-user", default="postgres", help="Administrador do PostgreSQL (padrão: postgres).")
        parser.add_argument("--admin-password", default=None,
                            help="Senha do administrador. Se omitida, será solicitada (não fica no histórico).")

    def handle(self, *args, **opts):
        try:
            import psycopg2
            from psycopg2 import sql
        except ImportError as exc:
            raise CommandError("Instale as dependências: pip install -r requirements.txt") from exc

        cfg = settings.DATABASES["default"]
        if "postgresql" not in cfg["ENGINE"]:
            raise CommandError("Defina DB_NAME, DB_USER e DB_PASSWORD (ou DATABASE_URL) no arquivo .env para usar "
                               "o PostgreSQL. Hoje o sistema está configurado para SQLite.")
        banco, usuario, senha = cfg["NAME"], cfg["USER"], cfg["PASSWORD"]
        if not usuario or not senha:
            raise CommandError("Defina DB_USER e DB_PASSWORD no arquivo .env.")
        admin_senha = opts["admin_password"] if opts["admin_password"] is not None else getpass(
            f"Senha do administrador do PostgreSQL ({opts['admin_user']}): ")
        try:
            con = psycopg2.connect(dbname="postgres", user=opts["admin_user"], password=admin_senha,
                                   host=cfg["HOST"] or "localhost", port=cfg["PORT"] or 5432)
        except psycopg2.OperationalError as exc:
            raise CommandError(f"Não foi possível conectar ao PostgreSQL em {cfg['HOST']}:{cfg['PORT']}. "
                               f"Confira se ele está instalado e em execução e se a senha está correta.\n{exc}") from exc
        con.autocommit = True  # CREATE DATABASE não pode rodar dentro de um bloco de transação
        try:
            cur = con.cursor()
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [usuario])
            if cur.fetchone():
                self.stdout.write(f"Usuário '{usuario}' já existe (senha mantida).")
            else:
                cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(usuario),
                                                                              sql.Literal(senha)))
                self.stdout.write(self.style.SUCCESS(f"Usuário '{usuario}' criado."))
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [banco])
            if cur.fetchone():
                self.stdout.write(f"Banco '{banco}' já existe.")
            else:
                cur.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(banco), sql.Identifier(usuario)))
                self.stdout.write(self.style.SUCCESS(f"Banco '{banco}' criado."))
        finally:
            con.close()
        self.stdout.write(self.style.SUCCESS("Pronto. Agora rode: python manage.py migrate"))
