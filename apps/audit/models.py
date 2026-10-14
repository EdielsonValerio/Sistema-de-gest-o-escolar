from django.conf import settings
from django.db import models


class RegistroAuditoria(models.Model):
    """Trilha de auditoria. A aplicação só insere; nunca edita nem exclui."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="auditoria")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="+")
    usuario_nome = models.CharField(max_length=150, blank=True)
    acao = models.CharField("ação", max_length=60)
    entidade = models.CharField(max_length=60)
    entidade_id = models.CharField(max_length=40, blank=True)
    aluno_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    descricao = models.CharField("descrição", max_length=255, blank=True)
    dados = models.JSONField("dados", default=dict, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "registro de auditoria"
        verbose_name_plural = "registros de auditoria"
        ordering = ["-criado_em", "-id"]
        indexes = [
            models.Index(fields=["entidade", "entidade_id"]),
            models.Index(fields=["criado_em"]),
        ]
        permissions = [("ver_auditoria", "Pode consultar a auditoria")]

    def __str__(self):
        return f"{self.criado_em:%d/%m/%Y %H:%M} {self.acao} {self.entidade}#{self.entidade_id}"
