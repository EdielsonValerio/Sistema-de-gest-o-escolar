from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("core", "0002_alter_instituicao_options_instituicao_criada_em_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="perfilusuario",
            name="login",
            field=models.CharField(default="", max_length=100, verbose_name="usuário (login)"),
        ),
    ]
