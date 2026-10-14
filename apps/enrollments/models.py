from django.conf import settings
from django.db import models
from django.db.models import F, Q


class Matricula(models.Model):
    """Vínculo do aluno a uma turma (oficial = matrícula; não oficial = inscrição)."""

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        ATIVA = "ATIVA", "Ativa"
        TRANSFERIDA = "TRANSFERIDA", "Transferida"
        CANCELADA = "CANCELADA", "Cancelada"
        CONCLUIDA = "CONCLUIDA", "Concluída"

    STATUS_VIGENTES = (Status.PENDENTE, Status.ATIVA)

    # Transições permitidas (ver docs/ARQUITETURA.md)
    TRANSICOES = {
        Status.PENDENTE: {Status.ATIVA, Status.CANCELADA},
        Status.ATIVA: {Status.TRANSFERIDA, Status.CONCLUIDA, Status.CANCELADA},
        Status.TRANSFERIDA: set(),
        Status.CANCELADA: set(),
        Status.CONCLUIDA: set(),
    }

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="matriculas")
    aluno = models.ForeignKey("students.Aluno", on_delete=models.PROTECT, related_name="matriculas")
    turma = models.ForeignKey("academic.Turma", on_delete=models.PROTECT, related_name="matriculas")
    # Denormalizados da turma no momento da matrícula (histórico + constraints de banco).
    tipo = models.CharField(max_length=12, choices=[("OFICIAL", "Oficial"), ("NAO_OFICIAL", "Não oficial")])
    ano_letivo = models.ForeignKey("academic.AnoLetivo", null=True, blank=True, on_delete=models.PROTECT,
                                   related_name="matriculas")
    numero = models.CharField("número de matrícula/inscrição", max_length=40, null=True, blank=True)
    status = models.CharField("situação", max_length=12, choices=Status.choices, default=Status.ATIVA)
    data_ingresso = models.DateField("data de ingresso")
    data_saida = models.DateField("data de saída", null=True, blank=True)
    matricula_anterior = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT,
                                              related_name="matricula_seguinte")
    observacoes = models.TextField("observações", blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "matrícula"
        verbose_name_plural = "matrículas"
        ordering = ["-criado_em", "-id"]
        permissions = [
            ("transferir_matricula", "Pode transferir matrícula"),
            ("cancelar_matricula", "Pode cancelar matrícula"),
        ]
        indexes = [models.Index(fields=["instituicao", "numero"]), models.Index(fields=["aluno", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["aluno", "ano_letivo"],
                condition=Q(tipo="OFICIAL", status__in=["PENDENTE", "ATIVA"]),
                name="uniq_matricula_oficial_vigente_por_ano"),
            models.UniqueConstraint(
                fields=["aluno", "turma"], condition=Q(status__in=["PENDENTE", "ATIVA"]),
                name="uniq_vinculo_vigente_aluno_turma"),
            models.UniqueConstraint(
                fields=["instituicao", "numero"], condition=Q(numero__isnull=False),
                name="uniq_matricula_numero"),
            models.CheckConstraint(
                condition=Q(data_saida__isnull=True) | Q(data_saida__gte=F("data_ingresso")),
                name="chk_matricula_datas"),
            models.CheckConstraint(
                condition=(Q(tipo="OFICIAL", ano_letivo__isnull=False) | Q(tipo="NAO_OFICIAL", ano_letivo__isnull=True)),
                name="chk_matricula_tipo_ano"),
        ]

    def __str__(self):
        return f"{self.numero or 's/n'} — {self.aluno.nome} — {self.turma.nome}"

    @property
    def vigente(self):
        return self.status in self.STATUS_VIGENTES

    def pode_transitar_para(self, novo):
        return novo in self.TRANSICOES[self.status]
