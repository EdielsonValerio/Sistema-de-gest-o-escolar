from django.db import migrations
from django.db.models import OuterRef, Subquery


def preencher(apps, schema_editor):
    """Herda a escola do registro pai (aluno, ano letivo, parcela ou recebimento)."""
    SerieOfertada = apps.get_model("academic", "SerieOfertada")
    Pai = apps.get_model("academic", "AnoLetivo")
    SerieOfertada.objects.filter(instituicao__isnull=True).update(
        instituicao_id=Subquery(Pai.objects.filter(pk=OuterRef("ano_letivo_id")).values("instituicao_id")[:1]))


class Migration(migrations.Migration):

    dependencies = [("academic", "0002_serieofertada_instituicao")]

    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
