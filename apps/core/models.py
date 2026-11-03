import os
import uuid

from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def logotipo_escola_path(instance, filename):
    """Nome do arquivo gerado (aleatório): não depende do nome enviado pelo usuário."""
    ext = os.path.splitext(filename)[1].lower()[:6]
    return f"escolas/logotipos/{uuid.uuid4().hex}{ext}"


class Instituicao(models.Model):
    """Escola (cliente do sistema). Todos os dados de negócio pertencem a exatamente uma instituição."""

    nome = models.CharField("nome da escola", max_length=150)
    cnpj = models.CharField("CNPJ", max_length=18, blank=True)
    logotipo = models.ImageField("logotipo", upload_to=logotipo_escola_path, blank=True)
    ativa = models.BooleanField("ativa", default=True,
                                help_text="Escolas inativas não aparecem no login e seus usuários perdem o acesso.")
    criada_em = models.DateTimeField("cadastrada em", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "instituição"
        verbose_name_plural = "instituições"
        ordering = ["nome"]
        constraints = [models.UniqueConstraint(Lower("nome"), name="uniq_instituicao_nome")]

    def __str__(self):
        return self.nome


class ConfiguracaoGeral(models.Model):
    instituicao = models.OneToOneField(Instituicao, on_delete=models.CASCADE, related_name="configuracao")
    exigir_plano_na_matricula = models.BooleanField(
        "exigir plano financeiro na matrícula",
        default=False,
        help_text="Se marcado, matrícula em turma com plano ativo exige a escolha de um plano.",
    )
    itens_por_pagina = models.PositiveSmallIntegerField("itens por página", default=20)
    permitir_saldo_negativo = models.BooleanField(
        "permitir saldo negativo no contas a pagar",
        default=False,
        help_text="Se desmarcado, não é possível liquidar uma conta a pagar maior que o saldo disponível "
                  "(total recebido menos o total já pago).",
    )
    dias_aviso_contas_pagar = models.PositiveSmallIntegerField(
        "avisar contas a pagar com antecedência de (dias)",
        default=5,
        help_text="As contas em aberto que vencem dentro deste prazo (e as vencidas) aparecem nas notificações.",
    )

    class Meta:
        verbose_name = "configuração geral"
        verbose_name_plural = "configurações gerais"

    def __str__(self):
        return f"Configurações de {self.instituicao}"


class TipoNumeracao(models.TextChoices):
    CODIGO_ALUNO = "CODIGO_ALUNO", "Código único do aluno"
    MATRICULA_OFICIAL = "MATRICULA_OFICIAL", "Matrícula (turma oficial)"
    INSCRICAO_NAO_OFICIAL = "INSCRICAO_NAO_OFICIAL", "Inscrição (turma não oficial)"
    RECEBIMENTO = "RECEBIMENTO", "Número do recebimento"


class EscopoNumeracao(models.TextChoices):
    INSTITUICAO = "INSTITUICAO", "Instituição"
    ANO_LETIVO = "ANO_LETIVO", "Ano letivo"
    SERIE = "SERIE", "Série"
    TURMA = "TURMA", "Turma"


class ConfiguracaoNumeracao(models.Model):
    instituicao = models.ForeignKey(Instituicao, on_delete=models.PROTECT, related_name="configuracoes_numeracao")
    tipo = models.CharField("tipo", max_length=30, choices=TipoNumeracao.choices)
    formato = models.CharField("formato", max_length=120)
    separador = models.CharField("separador", max_length=3, blank=True)
    tamanho_sequencial = models.PositiveSmallIntegerField("tamanho do sequencial", default=4)
    escopo = models.CharField("escopo da sequência", max_length=20, choices=EscopoNumeracao.choices,
                              default=EscopoNumeracao.ANO_LETIVO)
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "configuração de numeração"
        verbose_name_plural = "configurações de numeração"
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "tipo"], name="uniq_config_numeracao_tipo"),
            models.CheckConstraint(
                condition=models.Q(tamanho_sequencial__gte=1, tamanho_sequencial__lte=12),
                name="chk_numeracao_tamanho",
            ),
        ]

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.formato}"


class SequenciaNumeracao(models.Model):
    """Contador transacional. Nunca é decrementado nem reutilizado."""

    instituicao = models.ForeignKey(Instituicao, on_delete=models.PROTECT)
    tipo = models.CharField(max_length=30, choices=TipoNumeracao.choices)
    chave_escopo = models.CharField(max_length=80)
    ultimo_valor = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "sequência de numeração"
        verbose_name_plural = "sequências de numeração"
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "tipo", "chave_escopo"], name="uniq_sequencia_escopo"),
        ]

    def __str__(self):
        return f"{self.tipo}/{self.chave_escopo}={self.ultimo_valor}"
