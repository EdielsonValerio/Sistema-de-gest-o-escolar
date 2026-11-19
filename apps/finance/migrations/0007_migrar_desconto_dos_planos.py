from django.db import migrations


def migrar(apps, schema_editor):
    """O desconto saiu do plano. Planos que tinham % ganham um desconto equivalente no catálogo da escola
    (escolhido na cobrança, sem dia limite); parcelas e contratos já gerados não mudam."""
    Plano = apps.get_model("finance", "PlanoFinanceiro")
    Desconto = apps.get_model("finance", "Desconto")
    for plano in Plano.objects.filter(desconto_padrao_percentual__gt=0):
        pct = plano.desconto_padrao_percentual
        Desconto.objects.get_or_create(
            instituicao_id=plano.instituicao_id, nome=f"Desconto de {pct}% (plano anterior)",
            defaults=dict(tipo="PERCENTUAL", valor=pct, todos_os_planos=False, acumulativo=True, ativo=True))
        plano.desconto_padrao_percentual = 0
        plano.save(update_fields=["desconto_padrao_percentual"])


class Migration(migrations.Migration):
    dependencies = [("finance", "0006_descontos")]
    operations = [migrations.RunPython(migrar, migrations.RunPython.noop)]
