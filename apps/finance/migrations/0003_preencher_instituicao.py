from django.db import migrations
from django.db.models import OuterRef, Subquery


def preencher(apps, schema_editor):
    """Herda a escola do registro pai (aluno, ano letivo, parcela ou recebimento)."""
    AjusteParcela = apps.get_model("finance", "AjusteParcela")
    Pai = apps.get_model("finance", "Parcela")
    AjusteParcela.objects.filter(instituicao__isnull=True).update(
        instituicao_id=Subquery(Pai.objects.filter(pk=OuterRef("parcela_id")).values("instituicao_id")[:1]))

    AlocacaoRecebimento = apps.get_model("finance", "AlocacaoRecebimento")
    Pai = apps.get_model("finance", "Recebimento")
    AlocacaoRecebimento.objects.filter(instituicao__isnull=True).update(
        instituicao_id=Subquery(Pai.objects.filter(pk=OuterRef("recebimento_id")).values("instituicao_id")[:1]))

    Estorno = apps.get_model("finance", "Estorno")
    Pai = apps.get_model("finance", "Recebimento")
    Estorno.objects.filter(instituicao__isnull=True).update(
        instituicao_id=Subquery(Pai.objects.filter(pk=OuterRef("recebimento_id")).values("instituicao_id")[:1]))


class Migration(migrations.Migration):

    dependencies = [("finance", "0002_ajusteparcela_instituicao_and_more")]

    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
