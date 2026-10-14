from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _carregar_series_se_vazia(sender, **kwargs):
    """Na primeira migração, popula a tabela de referência (depois disso ela é administrável)."""
    from .models import SerieReferencia
    from .services import carregar_series_padrao

    if not SerieReferencia.objects.exists():
        carregar_series_padrao()


class AcademicConfig(AppConfig):
    name = "apps.academic"
    label = "academic"
    verbose_name = "Acadêmico"

    def ready(self):
        post_migrate.connect(_carregar_series_se_vazia, sender=self, dispatch_uid="carregar_series_se_vazia")
