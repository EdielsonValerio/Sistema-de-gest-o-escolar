"""Comunicados por WhatsApp (Meta Cloud API). O token da escola fica cifrado (mesmo cofre da cobrança bancária)."""
from django.conf import settings
from django.db import models


class ConfiguracaoWhatsApp(models.Model):
    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE, related_name="configuracao_whatsapp")
    ativo = models.BooleanField("envio ativo", default=False)
    phone_number_id = models.CharField("ID do número de telefone", max_length=40, blank=True,
                                       help_text="Phone number ID, no painel WhatsApp > Configuração da API (Meta).")
    waba_id = models.CharField("ID da conta do WhatsApp Business", max_length=40, blank=True)
    token_cifrado = models.TextField(blank=True, editable=False)
    template_nome = models.CharField("nome do modelo de mensagem aprovado", max_length=80, blank=True,
                                     help_text="Modelo (template) aprovado pela Meta com UMA variável no corpo: {{1}}.")
    template_idioma = models.CharField("idioma do modelo", max_length=10, default="pt_BR")
    versao_api = models.CharField("versão da API", max_length=10, default="v21.0")
    testada_em = models.DateTimeField(null=True, blank=True, editable=False)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuração do WhatsApp"
        verbose_name_plural = "configurações do WhatsApp"
        permissions = [("configurar_whatsapp", "Pode configurar a API do WhatsApp")]

    @classmethod
    def obter(cls, instituicao):
        return cls.objects.get_or_create(instituicao=instituicao)[0]

    @property
    def completa(self):
        return bool(self.phone_number_id and self.token_cifrado and self.template_nome)

    def __str__(self):
        return f"WhatsApp de {self.instituicao_id}"


class Comunicado(models.Model):
    class Alvo(models.TextChoices):
        RESPONSAVEL = "RESPONSAVEL", "Um responsável"
        TURMAS = "TURMAS", "Uma ou mais turmas"
        ESCOLA = "ESCOLA", "Toda a escola"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="comunicados")
    alvo = models.CharField(max_length=12, choices=Alvo.choices)
    responsavel = models.ForeignKey("students.Responsavel", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    turmas = models.ManyToManyField("academic.Turma", blank=True, related_name="+")
    mensagem = models.CharField("mensagem", max_length=900)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "comunicado"
        ordering = ["-criado_em"]
        permissions = [("enviar_comunicado", "Pode enviar comunicados por WhatsApp")]

    def __str__(self):
        return f"Comunicado {self.pk}"

    def resumo(self):
        return {s: self.envios.filter(status=s).count() for s, _ in EnvioComunicado.Status.choices}


class EnvioComunicado(models.Model):
    """Um destinatário de um comunicado. Guarda só o telefone mascarado."""

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Na fila"
        ENVIADO = "ENVIADO", "Enviado"
        FALHA = "FALHA", "Falhou"
        SEM_WHATSAPP = "SEM_WHATSAPP", "Sem WhatsApp válido"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    comunicado = models.ForeignKey(Comunicado, on_delete=models.CASCADE, related_name="envios")
    responsavel = models.ForeignKey("students.Responsavel", on_delete=models.PROTECT, related_name="+")
    telefone_mascarado = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.PENDENTE)
    erro = models.CharField(max_length=200, blank=True)
    message_id = models.CharField(max_length=120, blank=True)
    enviado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "envio de comunicado"
        constraints = [models.UniqueConstraint(fields=["comunicado", "responsavel"], name="uniq_envio_comunicado")]
        ordering = ["responsavel__nome"]
