"""Portal do responsável.

Conta PRÓPRIA, separada dos usuários da escola (`auth.User`): o responsável nunca é um usuário da equipe, então não
existe caminho para as telas internas. A autorização é sempre por ALUNO (`AcessoAluno`), com duas permissões
independentes: financeiro e pedagógico. A escola liberta/revoga tudo pela ficha do responsável.
"""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

VALIDADE_CONVITE = timedelta(days=3)


class ConfiguracaoPortal(models.Model):
    """Link público (por escola) de entrada do portal e chave geral de liga/desliga."""

    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE, related_name="configuracao_portal")
    ativo = models.BooleanField("portal ativo", default=False,
                                help_text="Desligado, ninguém desta escola consegue entrar no portal.")
    slug = models.SlugField("endereço da escola", max_length=70, unique=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração do portal"
        verbose_name_plural = "configurações do portal"
        permissions = [("configurar_portal", "Pode configurar o portal do responsável")]

    def __str__(self):
        return f"Portal de {self.instituicao}"

    @classmethod
    def obter(cls, instituicao):
        cfg = cls.objects.filter(instituicao=instituicao).first()
        if cfg:
            return cfg
        base = slugify(instituicao.nome)[:50] or "escola"
        slug = base
        while cls.objects.filter(slug=slug).exists():
            slug = f"{base}-{secrets.token_hex(2)}"
        return cls.objects.create(instituicao=instituicao, slug=slug)


class AcessoResponsavel(models.Model):
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="acessos_portal")
    responsavel = models.OneToOneField("students.Responsavel", on_delete=models.CASCADE, related_name="acesso_portal")
    login = models.CharField("login (CPF)", max_length=14, help_text="Somente dígitos do CPF do responsável.")
    liberado = models.BooleanField("acesso liberado pela escola", default=False)
    senha_hash = models.CharField(max_length=256, blank=True, editable=False)
    convite_hash = models.CharField(max_length=64, blank=True, editable=False)
    convite_expira = models.DateTimeField(null=True, blank=True, editable=False)
    ultimo_acesso = models.DateTimeField(null=True, blank=True, editable=False)
    revogado_em = models.DateTimeField(null=True, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)

    class Meta:
        verbose_name = "acesso ao portal"
        verbose_name_plural = "acessos ao portal"
        permissions = [("gerenciar_acesso_portal", "Pode liberar e revogar acessos ao portal")]
        constraints = [models.UniqueConstraint(fields=["instituicao", "login"], name="uniq_acesso_portal_login")]

    def __str__(self):
        return f"Acesso de {self.responsavel_id}"

    @property
    def tem_senha(self):
        return bool(self.senha_hash)

    def definir_senha(self, bruta):
        self.senha_hash = make_password(bruta)
        self.convite_hash, self.convite_expira = "", None  # o convite é de uso único

    def conferir_senha(self, bruta):
        return bool(self.senha_hash) and check_password(bruta, self.senha_hash)

    @staticmethod
    def _hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def gerar_convite(self):
        """Devolve o token em claro (único momento em que ele existe); no banco fica só o hash."""
        token = secrets.token_urlsafe(32)
        self.convite_hash, self.convite_expira = self._hash(token), timezone.now() + VALIDADE_CONVITE
        self.save(update_fields=["convite_hash", "convite_expira"])
        return token

    @classmethod
    def por_convite(cls, token):
        if not token or len(token) > 100:
            return None
        a = cls.objects.select_related("instituicao", "responsavel").filter(convite_hash=cls._hash(token)).first()
        if a is None or a.convite_expira is None or a.convite_expira < timezone.now():
            return None
        return a

    @property
    def marca_de_sessao(self):
        """Muda quando a senha muda: sessões abertas com a senha antiga deixam de valer."""
        return hashlib.sha256(self.senha_hash.encode()).hexdigest()[:16]


class AcessoAluno(models.Model):
    """O que o responsável pode ver de CADA aluno. Sem linha aqui, o aluno não existe para ele."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    acesso = models.ForeignKey(AcessoResponsavel, on_delete=models.CASCADE, related_name="alunos")
    aluno = models.ForeignKey("students.Aluno", on_delete=models.PROTECT, related_name="acessos_portal")
    financeiro = models.BooleanField("financeiro", default=True,
                                     help_text="Parcelas, boletos e recibos deste aluno (só as cobranças em nome dele).")
    pedagogico = models.BooleanField("pedagógico", default=False,
                                     help_text="Dados do aluno e boletim (só as fases já liberadas).")

    class Meta:
        verbose_name = "acesso ao aluno"
        verbose_name_plural = "acessos aos alunos"
        constraints = [models.UniqueConstraint(fields=["acesso", "aluno"], name="uniq_acesso_aluno")]

    def __str__(self):
        return f"{self.acesso_id} → {self.aluno_id}"


class DocumentoPortal(models.Model):
    """Documentos que o próprio responsável gerou no portal (só ele os reabre)."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    acesso = models.ForeignKey(AcessoResponsavel, on_delete=models.CASCADE, related_name="documentos")
    documento = models.OneToOneField("documents.DocumentoEmitido", on_delete=models.PROTECT, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em", "-id"]
