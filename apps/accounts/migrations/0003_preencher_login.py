from django.db import migrations


def preencher(apps, schema_editor):
    """O login passa a ser o username atual (sem prefixo interno, se houver)."""
    Perfil = apps.get_model("accounts", "PerfilUsuario")
    for perfil in Perfil.objects.select_related("user").filter(login=""):
        u = perfil.user.username
        perfil.login = u.split(".", 1)[1] if u[:1].isdigit() and "." in u else u
        perfil.save(update_fields=["login"])


class Migration(migrations.Migration):

    dependencies = [("accounts", "0002_perfilusuario_login")]

    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
