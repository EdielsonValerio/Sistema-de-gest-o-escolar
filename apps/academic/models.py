from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class AnoLetivo(models.Model):
    class Situacao(models.TextChoices):
        PLANEJAMENTO = "PLANEJAMENTO", "Planejamento"
        ATIVO = "ATIVO", "Ativo"
        ENCERRADO = "ENCERRADO", "Encerrado"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="anos_letivos")
    nome = models.CharField("nome/descrição", max_length=80)
    ano = models.PositiveSmallIntegerField("ano de referência")
    data_inicio = models.DateField("data de início")
    data_fim = models.DateField("data de término")
    situacao = models.CharField("situação", max_length=12, choices=Situacao.choices, default=Situacao.PLANEJAMENTO)

    class Meta:
        verbose_name = "ano letivo"
        verbose_name_plural = "anos letivos"
        ordering = ["-ano"]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "ano"], name="uniq_ano_letivo"),
            models.CheckConstraint(condition=Q(data_fim__gte=F("data_inicio")), name="chk_ano_letivo_datas"),
        ]

    def __str__(self):
        return self.nome

    def clean(self):
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            raise ValidationError({"data_fim": "A data de término não pode ser anterior à data de início."})


class SerieReferencia(models.Model):
    """Tabela de referência (administrável) da estrutura educacional brasileira.

    Os códigos são INTERNOS e estáveis. `codigo_oficial` fica livre para quem quiser
    registrar uma classificação oficial (com a origem em `fonte_codigo_oficial`).
    """

    class Etapa(models.TextChoices):
        EDUCACAO_INFANTIL = "EI", "Educação Infantil"
        FUNDAMENTAL_INICIAIS = "EF1", "Ensino Fundamental — anos iniciais"
        FUNDAMENTAL_FINAIS = "EF2", "Ensino Fundamental — anos finais"
        MEDIO = "EM", "Ensino Médio"
        OUTRAS = "OUT", "Outras etapas e modalidades"

    codigo = models.CharField("código interno", max_length=20, unique=True)
    nome = models.CharField("nome", max_length=80)
    etapa = models.CharField("etapa", max_length=3, choices=Etapa.choices)
    ordem = models.PositiveSmallIntegerField("ordem", help_text="Usada no número de matrícula ([NumeroSerie]).")
    codigo_oficial = models.CharField("código oficial", max_length=20, blank=True)
    fonte_codigo_oficial = models.CharField("origem do código oficial", max_length=120, blank=True)
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "série de referência"
        verbose_name_plural = "séries de referência"
        ordering = ["etapa", "ordem"]

    def __str__(self):
        return self.nome


class SerieOfertada(models.Model):
    class Situacao(models.TextChoices):
        ATIVA = "ATIVA", "Ativa"
        ENCERRADA = "ENCERRADA", "Encerrada"

    ano_letivo = models.ForeignKey(AnoLetivo, on_delete=models.PROTECT, related_name="series_ofertadas")
    serie_referencia = models.ForeignKey(SerieReferencia, on_delete=models.PROTECT, related_name="ofertas")
    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.ATIVA)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "série ofertada"
        verbose_name_plural = "séries ofertadas"
        ordering = ["-ano_letivo__ano", "serie_referencia__etapa", "serie_referencia__ordem"]
        constraints = [
            models.UniqueConstraint(fields=["ano_letivo", "serie_referencia"], name="uniq_serie_ofertada"),
        ]

    def __str__(self):
        return f"{self.serie_referencia.nome} — {self.ano_letivo.ano}"

    @property
    def instituicao_id(self):
        return self.ano_letivo.instituicao_id


class Turma(models.Model):
    class Tipo(models.TextChoices):
        OFICIAL = "OFICIAL", "Oficial"
        NAO_OFICIAL = "NAO_OFICIAL", "Não oficial"

    class Situacao(models.TextChoices):
        PLANEJADA = "PLANEJADA", "Planejada"
        ATIVA = "ATIVA", "Ativa"
        ENCERRADA = "ENCERRADA", "Encerrada"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="turmas")
    codigo = models.CharField("código interno", max_length=20, editable=False)
    numero = models.PositiveSmallIntegerField(
        "identificador numérico", help_text="Estável; usado em [NumeroTurma]. Independe do nome.")
    nome = models.CharField("nome de exibição", max_length=100)
    tipo = models.CharField("tipo", max_length=12, choices=Tipo.choices, default=Tipo.OFICIAL)
    ano_letivo = models.ForeignKey(AnoLetivo, null=True, blank=True, on_delete=models.PROTECT, related_name="turmas")
    serie_ofertada = models.ForeignKey(SerieOfertada, null=True, blank=True, on_delete=models.PROTECT,
                                       related_name="turmas")
    ano_referencia = models.PositiveSmallIntegerField("ano de referência", null=True, blank=True)
    data_inicio = models.DateField("data de início", null=True, blank=True)
    data_fim = models.DateField("data de término", null=True, blank=True)
    categoria = models.CharField("modalidade/categoria", max_length=80, blank=True)
    capacidade = models.PositiveSmallIntegerField("capacidade máxima", null=True, blank=True)
    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.PLANEJADA)
    exige_numero_matricula = models.BooleanField("gera número de matrícula/inscrição", default=True)
    observacoes = models.TextField("observações", blank=True)

    class Meta:
        verbose_name = "turma"
        verbose_name_plural = "turmas"
        ordering = ["-ano_letivo__ano", "-ano_referencia", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "codigo"], name="uniq_turma_codigo"),
            # Número único dentro da série (oficial) ou do ano de referência (não oficial).
            models.UniqueConstraint(fields=["serie_ofertada", "numero"], condition=Q(serie_ofertada__isnull=False),
                                    name="uniq_turma_numero_serie"),
            models.UniqueConstraint(fields=["instituicao", "ano_referencia", "numero"],
                                    condition=Q(tipo="NAO_OFICIAL"), name="uniq_turma_numero_nao_oficial"),
            models.CheckConstraint(
                condition=Q(data_inicio__isnull=True) | Q(data_fim__isnull=True) | Q(data_fim__gte=F("data_inicio")),
                name="chk_turma_datas"),
            models.CheckConstraint(
                condition=(
                    Q(tipo="OFICIAL", serie_ofertada__isnull=False, ano_letivo__isnull=False)
                    | Q(tipo="NAO_OFICIAL", serie_ofertada__isnull=True, ano_letivo__isnull=True,
                        ano_referencia__isnull=False, data_inicio__isnull=False, data_fim__isnull=False)
                ),
                name="chk_turma_tipo_coerente"),
        ]

    def __str__(self):
        return self.nome

    @property
    def eh_oficial(self):
        return self.tipo == self.Tipo.OFICIAL

    @property
    def ano_exibicao(self):
        return self.ano_letivo.ano if self.eh_oficial else self.ano_referencia

    def clean(self):
        erros = {}
        if self.tipo == self.Tipo.OFICIAL:
            if not self.serie_ofertada_id:
                erros["serie_ofertada"] = "Turma oficial exige uma série ofertada."
            if not self.ano_letivo_id:
                erros["ano_letivo"] = "Turma oficial exige um ano letivo."
            if self.serie_ofertada_id and self.ano_letivo_id and self.serie_ofertada.ano_letivo_id != self.ano_letivo_id:
                erros["serie_ofertada"] = "A série ofertada pertence a outro ano letivo."
            if self.ano_referencia:
                erros["ano_referencia"] = "Turma oficial não usa ano de referência."
        else:
            if self.serie_ofertada_id or self.ano_letivo_id:
                erros["serie_ofertada"] = "Turma não oficial não se vincula a série nem a ano letivo."
            if not self.ano_referencia:
                erros["ano_referencia"] = "Informe o ano de referência."
            if not self.data_inicio or not self.data_fim:
                erros["data_inicio"] = "Turma não oficial exige período (início e término)."
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            erros["data_fim"] = "A data de término não pode ser anterior à data de início."
        if erros:
            raise ValidationError(erros)
