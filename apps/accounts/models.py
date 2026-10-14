from django.conf import settings
from django.db import models


class PerfilUsuario(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="perfis")

    class Meta:
        verbose_name = "perfil de usuário"
        verbose_name_plural = "perfis de usuário"
        permissions = [("gerenciar_usuarios", "Pode gerenciar usuários e permissões")]

    def __str__(self):
        return f"Perfil de {self.user}"
