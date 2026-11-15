from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class PerfilUsuario(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="perfis")
    # Login exibido e digitado pelo usuário: único DENTRO da escola (duas escolas podem ter "secretaria").
    # O `User.username` do Django é interno e global (gerado como "<id da escola>.<login>").
    login = models.CharField("usuário (login)", max_length=100, default="")
    # Senha provisória (definida por outra pessoa): o usuário só usa o sistema depois de criar a própria senha.
    deve_trocar_senha = models.BooleanField("deve trocar a senha no próximo acesso", default=False)
    senha_trocada_em = models.DateTimeField("última troca de senha pelo próprio usuário", null=True, blank=True)

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


class TentativaLogin(models.Model):
    """Registro de falhas de autenticação/pedidos sensíveis, para limitar tentativas (força bruta).

    `chave` guarda somente um hash (HMAC) do IP ou da conta, nunca o login nem a senha em texto. É global e
    temporário (limpo por `python manage.py limpar_seguranca`)."""

    class Tipo(models.TextChoices):
        LOGIN_CONTA = "LOGIN_CONTA", "Falha de login (conta)"
        LOGIN_IP = "LOGIN_IP", "Falha de login (IP)"
        RECUPERACAO_CONTA = "RECUP_CONTA", "Pedido de recuperação (conta)"
        RECUPERACAO_IP = "RECUP_IP", "Pedido de recuperação (IP)"
        MFA_CONTA = "MFA_CONTA", "Falha de MFA (conta)"
        WEBHOOK_IP = "WEBHOOK_IP", "Falha de token de webhook (IP)"

    tipo = models.CharField(max_length=12, choices=Tipo.choices)
    chave = models.CharField(max_length=64)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["tipo", "chave", "criado_em"])]


class TokenConta(models.Model):
    """Token de uso único (ativação de conta ou redefinição de senha). Só o hash SHA-256 é gravado."""

    class Finalidade(models.TextChoices):
        ATIVACAO = "ATIVACAO", "Ativação de conta"
        RECUPERACAO = "RECUPERACAO", "Redefinição de senha"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tokens_conta")
    finalidade = models.CharField(max_length=12, choices=Finalidade.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    usado_em = models.DateTimeField(null=True, blank=True)


class MfaDispositivo(models.Model):
    """Segundo fator TOTP (RFC 6238) do usuário. O segredo é gravado cifrado; códigos de recuperação, só em hash."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mfa")
    segredo_cifrado = models.TextField()
    confirmado = models.BooleanField(default=False)
    ultimo_passo = models.BigIntegerField(default=0, help_text="Último intervalo de 30 s aceito (impede reutilizar o código).")
    codigos_recuperacao = models.JSONField(default=list, help_text="Hashes SHA-256 dos códigos de recuperação não usados.")
    criado_em = models.DateTimeField(auto_now_add=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
