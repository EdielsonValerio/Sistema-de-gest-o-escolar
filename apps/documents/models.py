from django.conf import settings
from django.db import models
from django.db.models import Q

from .tipos import CHOICES_TIPO


class Destinatario(models.TextChoices):
    ALUNO = "ALUNO", "Aluno"
    RESPONSAVEL = "RESPONSAVEL", "Responsável"


class CabecalhoDocumento(models.Model):
    """Cabeçalho padrão (e rodapé) aplicado aos documentos que o utilizam. Um por instituição."""

    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE, related_name="cabecalho_documento")
    ativo = models.BooleanField("usar cabeçalho padrão nos documentos", default=False)
    exibir_logotipo = models.BooleanField("exibir logotipo", default=True)
    logotipo = models.ImageField("logotipo da escola", upload_to="logotipos/", blank=True)
    titulo = models.CharField("título do cabeçalho", max_length=150, blank=True,
                              help_text="Se vazio, usa o nome da instituição.")
    linhas = models.TextField("linhas complementares", max_length=500, blank=True,
                              help_text="Endereço, telefone, e-mail, ato de autorização… (uma por linha).")
    rodape = models.CharField("rodapé", max_length=250, blank=True)

    class Meta:
        verbose_name = "cabeçalho de documentos"
        verbose_name_plural = "cabeçalhos de documentos"

    def __str__(self):
        return f"Cabeçalho de {self.instituicao}"


class ModeloDocumento(models.Model):
    """Modelo (leiaute) de um tipo de documento, para aluno ou responsável."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="modelos_documento")
    nome = models.CharField("nome do modelo", max_length=120)
    tipo = models.CharField("tipo de documento", max_length=30, choices=CHOICES_TIPO)
    destinatario = models.CharField("documento para", max_length=12, choices=Destinatario.choices,
                                    default=Destinatario.ALUNO)
    conteudo = models.TextField("leiaute do documento")  # HTML sanitizado com [variaveis]
    usar_cabecalho = models.BooleanField("inserir cabeçalho padrão", default=True)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "modelo de documento"
        verbose_name_plural = "modelos de documento"
        ordering = ["tipo", "nome"]
        permissions = [("emitir_documento", "Pode emitir documentos")]
        indexes = [models.Index(fields=["instituicao", "destinatario", "ativo"])]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "nome"], name="uniq_modelo_documento_nome"),
        ]

    def __str__(self):
        return self.nome


class DocumentoEmitido(models.Model):
    """Instantâneo do documento gerado (permite reimpressão idêntica; nunca editado nem excluído)."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="documentos_emitidos")
    modelo = models.ForeignKey(ModeloDocumento, on_delete=models.PROTECT, related_name="emissoes")
    tipo = models.CharField(max_length=30, choices=CHOICES_TIPO)
    titulo = models.CharField(max_length=120)
    destinatario = models.CharField(max_length=12, choices=Destinatario.choices)
    aluno = models.ForeignKey("students.Aluno", null=True, blank=True, on_delete=models.PROTECT,
                              related_name="documentos_emitidos")
    responsavel = models.ForeignKey("students.Responsavel", null=True, blank=True, on_delete=models.PROTECT,
                                    related_name="documentos_emitidos")
    html = models.TextField()
    emitido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="+")
    emitido_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "documento emitido"
        verbose_name_plural = "documentos emitidos"
        ordering = ["-emitido_em", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(destinatario="ALUNO", aluno__isnull=False)
                           | Q(destinatario="RESPONSAVEL", responsavel__isnull=False)),
                name="chk_documento_destinatario"),
        ]

    def __str__(self):
        return f"{self.titulo} nº {self.pk}"

    @property
    def numero(self):
        return f"{self.emitido_em:%Y}/{self.pk:06d}"
