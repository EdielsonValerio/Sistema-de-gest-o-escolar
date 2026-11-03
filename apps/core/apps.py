from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"
    verbose_name = "Núcleo"

    def ready(self):
        from . import tenant
        tenant.conectar()
