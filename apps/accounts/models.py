from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class PerfilUsuario(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="perfis")
    # Login exibido e digitado pelo usuário: único DENTRO da escola (duas escolas podem ter "secretaria").
    # O `User.username` do Django é interno e global (gerado como "<id da escola>.<login>").
    login = models.CharField("usuário (login)", max_length=100, default="")

    class Meta:
        verbose_name = "perfil de usuário"
        verbose_name_plural = "perfis de usuário"
        constraints = [models.UniqueConstraint("instituicao", Lower("login"), name="uniq_perfil_login_escola")]
        permissions = [("gerenciar_usuarios", "Pode gerenciar usuários e permissões")]

    def save(self, *args, **kwargs):
        if not self.login:  # compatibilidade: deriva do username interno ("<escola>.<login>" ou legado)
            self.login = self.user.username.split(".", 1)[1] if self.user.username[:1].isdigit() and "." in self.user.username else self.user.username
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Perfil de {self.user}"
