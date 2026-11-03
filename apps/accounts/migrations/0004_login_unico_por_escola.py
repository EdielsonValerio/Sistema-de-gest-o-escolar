import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("accounts", "0003_preencher_login")]

    operations = [
        migrations.AddConstraint(
            model_name="perfilusuario",
            constraint=models.UniqueConstraint(
                models.F("instituicao"), django.db.models.functions.text.Lower("login"),
                name="uniq_perfil_login_escola"),
        ),
    ]
