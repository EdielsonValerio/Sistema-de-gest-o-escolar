from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Contas e permissões"

    def ready(self):
        from .permissions import sincronizar_perfis
        post_migrate.connect(sincronizar_perfis, sender=self, dispatch_uid="sincronizar_perfis")
