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

    # Integridade da trilha: registros existentes nunca são alterados nem apagados pela aplicação.
    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise PermissionError("Registros de auditoria são imutáveis.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Registros de auditoria não podem ser excluídos.")


class EventoSeguranca(models.Model):
    """Eventos de segurança (login bloqueado, falha de webhook, MFA...), imutáveis. Não guarda senhas nem tokens."""

    class Tipo(models.TextChoices):
        LOGIN_FALHA = "LOGIN_FALHA", "Falha de login"
        LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO", "Login bloqueado temporariamente"
        RECUPERACAO_PEDIDA = "RECUPERACAO_PEDIDA", "Recuperação de senha solicitada"
        SENHA_REDEFINIDA = "SENHA_REDEFINIDA", "Senha redefinida por token"
        CONTA_ATIVADA = "CONTA_ATIVADA", "Conta ativada por token"
        TOKEN_INVALIDO = "TOKEN_INVALIDO", "Token inválido, expirado ou reutilizado"
        MFA_ATIVADA = "MFA_ATIVADA", "MFA ativada"
        MFA_FALHA = "MFA_FALHA", "Falha de MFA"
        MFA_RECUPERACAO_USADA = "MFA_RECUPERACAO_USADA", "Código de recuperação de MFA usado"
        WEBHOOK_FALHA = "WEBHOOK_FALHA", "Falha de autenticação de webhook"
        ACESSO_NEGADO = "ACESSO_NEGADO", "Acesso negado"
        LIMITE_TAXA = "LIMITE_TAXA", "Limite de requisições excedido"

    tipo = models.CharField(max_length=24, choices=Tipo.choices)
    instituicao = models.ForeignKey("core.Instituicao", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ip = models.GenericIPAddressField(null=True, blank=True)
    detalhe = models.CharField(max_length=255, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-criado_em", "-id"]
        indexes = [models.Index(fields=["tipo", "criado_em"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionError("Eventos de segurança são imutáveis.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Eventos de segurança não podem ser excluídos.")
