from django.db import migrations
from django.db.models import OuterRef, Subquery


def preencher(apps, schema_editor):
    """Herda a escola do registro pai (aluno, ano letivo, parcela ou recebimento)."""
    AlunoResponsavelFinanceiro = apps.get_model("students", "AlunoResponsavelFinanceiro")
    Pai = apps.get_model("students", "Aluno")
    AlunoResponsavelFinanceiro.objects.filter(instituicao__isnull=True).update(
        instituicao_id=Subquery(Pai.objects.filter(pk=OuterRef("aluno_id")).values("instituicao_id")[:1]))


class Migration(migrations.Migration):

    dependencies = [("students", "0002_alunoresponsavelfinanceiro_instituicao_and_more")]

    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
