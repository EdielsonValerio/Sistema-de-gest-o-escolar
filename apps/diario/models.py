"""Diário de Classe. Reaproveita usuários (professor/coordenador), turmas, matrículas e alunos já existentes.

Toda tabela tem `instituicao`. Registros pedagógicos guardam o professor e a turma/disciplina da época, de modo que
desativar um usuário ou encerrar uma alocação nunca apaga o histórico.
"""
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone


class Disciplina(models.Model):
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="disciplinas")
    nome = models.CharField("nome", max_length=80)
    sigla = models.CharField("sigla", max_length=12, blank=True)
    carga_horaria = models.PositiveSmallIntegerField(
        "carga horária anual (horas)", null=True, blank=True, validators=[MaxValueValidator(2000)],
        help_text="Opcional. Aparece no histórico escolar.")
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        ordering = ["nome"]
        constraints = [models.UniqueConstraint("instituicao", Lower("nome"), name="uniq_disciplina_nome")]

    def __str__(self):
        return self.nome


class PeriodoLetivo(models.Model):
    """Bimestre/trimestre/semestre de um ano letivo."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    ano_letivo = models.ForeignKey("academic.AnoLetivo", on_delete=models.PROTECT, related_name="periodos")
    nome = models.CharField("nome", max_length=40)
    ordem = models.PositiveSmallIntegerField("ordem")
    data_inicio = models.DateField("início")
    data_fim = models.DateField("término")
    # Fase de um Modelo de Avaliação (null = período geral do ano, usado pela regra padrão da escola)
    modelo = models.ForeignKey("diario.ModeloAvaliacao", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="fases", editable=False)
    peso = models.DecimalField("peso da fase na nota final", max_digits=5, decimal_places=2, default=Decimal("1.00"),
                               validators=[MinValueValidator(Decimal("0.01"))])
    formula = models.CharField("fórmula da nota da fase", max_length=300, blank=True,
                               help_text="Vazio = soma dos componentes. Ex.: ATV + PROVA, ou (ATV + PROVA) / 2")

    class Meta:
        ordering = ["ano_letivo", "modelo_id", "ordem"]
        constraints = [
            models.UniqueConstraint(fields=["ano_letivo", "ordem"], condition=Q(modelo__isnull=True), name="uniq_periodo_ordem"),
            models.UniqueConstraint(fields=["modelo", "ordem"], condition=Q(modelo__isnull=False), name="uniq_fase_ordem"),
        ]

    def __str__(self):
        return f"{self.nome} ({self.ano_letivo.ano})"


class ModeloAvaliacao(models.Model):
    """Regra de avaliação de uma escola/ano letivo, aplicável a séries específicas (parametrizada pelo administrador do
    sistema). Sem modelo publicado para a série, vale a regra padrão (ConfiguracaoDiario)."""

    class TipoFase(models.TextChoices):
        BIMESTRAL = "BIMESTRAL", "Bimestral (4 fases)"
        TRIMESTRAL = "TRIMESTRAL", "Trimestral (3 fases)"
        SEMESTRAL = "SEMESTRAL", "Semestral (2 fases)"
        MENSAL = "MENSAL", "Mensal (10 fases)"

    class Situacao(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        PUBLICADO = "PUBLICADO", "Publicado"

    class RegraRecParcial(models.TextChoices):
        MAIOR = "MAIOR", "Vale a maior nota (fase ou recuperação)"
        SUBSTITUI = "SUBSTITUI", "A recuperação substitui a nota da fase"
        MEDIA = "MEDIA", "Média entre a fase e a recuperação"

    class Arredondamento(models.TextChoices):
        COMERCIAL = "COMERCIAL", "Comercial (0,5 sobe)"
        CIMA = "CIMA", "Sempre para cima"
        BAIXO = "BAIXO", "Sempre para baixo"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="modelos_avaliacao")
    ano_letivo = models.ForeignKey("academic.AnoLetivo", on_delete=models.PROTECT, related_name="modelos_avaliacao")
    nome = models.CharField("nome do modelo", max_length=80)
    tipo_fase = models.CharField("fases avaliativas", max_length=12, choices=TipoFase.choices, default=TipoFase.BIMESTRAL)
    series = models.ManyToManyField("academic.SerieReferencia", blank=True, related_name="+",
                                    verbose_name="séries atendidas",
                                    help_text="Deixe vazio para valer em todas as séries sem modelo próprio.")
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.RASCUNHO)
    nota_maxima = models.DecimalField("nota máxima da fase e da média", max_digits=5, decimal_places=2, default=Decimal("10.00"),
                                      validators=[MinValueValidator(Decimal("0.01"))])
    media_aprovacao = models.DecimalField("nota mínima de aprovação", max_digits=5, decimal_places=2, default=Decimal("6.00"),
                                          validators=[MinValueValidator(0)])
    frequencia_minima = models.DecimalField("frequência mínima (%)", max_digits=5, decimal_places=2, default=Decimal("75.00"),
                                            validators=[MinValueValidator(0), MaxValueValidator(100)])
    casas_decimais = models.PositiveSmallIntegerField("casas decimais", default=2, validators=[MaxValueValidator(3)])
    arredondamento = models.CharField("arredondamento", max_length=10, choices=Arredondamento.choices,
                                      default=Arredondamento.COMERCIAL)
    formula_final = models.CharField("fórmula da média anual", max_length=300, blank=True,
                                     help_text="Vazio = média ponderada pelos pesos das fases (MP). Ex.: (B1 + B2 + B3 + B4) / 4")
    usa_rec_parcial = models.BooleanField("tem recuperação de fase (parcial)", default=False)
    regra_rec_parcial = models.CharField("regra da recuperação de fase", max_length=10, choices=RegraRecParcial.choices,
                                         default=RegraRecParcial.MAIOR)
    usa_rec_final = models.BooleanField("tem recuperação final", default=False)
    formula_rec_final = models.CharField("fórmula após a recuperação final", max_length=300, blank=True,
                                         help_text="Vazio = MAX(MF, (MF + RF) / 2). Variáveis: MF (média anual), RF, MA, MP, MAXIMA")
    media_aprovacao_rec_final = models.DecimalField("nota mínima após a recuperação final", max_digits=5, decimal_places=2,
                                                    null=True, blank=True, validators=[MinValueValidator(0)],
                                                    help_text="Vazio = a mesma nota mínima de aprovação.")
    criado_em = models.DateTimeField(auto_now_add=True)
    publicado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ano_letivo__ano", "nome"]
        constraints = [models.UniqueConstraint(fields=["instituicao", "ano_letivo", "nome"], name="uniq_modelo_avaliacao_nome")]

    def __str__(self):
        return f"{self.nome} ({self.ano_letivo.ano})"


class ComponenteNota(models.Model):
    """Parte da nota de uma fase (ex.: Atividades = 4,0 pontos, Prova = 6,0 pontos)."""

    class Agregacao(models.TextChoices):
        SOMA = "SOMA", "Soma das notas (limitada ao valor do componente)"
        MEDIA = "MEDIA", "Média simples das avaliações"
        PONDERADA = "PONDERADA", "Média ponderada pelo peso das avaliações"
        MAIOR = "MAIOR", "Maior nota"
        DESC_MENOR = "DESC_MENOR", "Média descartando a menor nota"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    fase = models.ForeignKey(PeriodoLetivo, on_delete=models.CASCADE, related_name="componentes")
    nome = models.CharField("nome", max_length=60)
    sigla = models.CharField("sigla (variável da fórmula)", max_length=12,
                             help_text="Letras maiúsculas, números e _; começa com letra. Ex.: ATV, PROVA")
    valor_maximo = models.DecimalField("valor máximo (pontos)", max_digits=5, decimal_places=2,
                                       validators=[MinValueValidator(Decimal("0.01"))])
    agregacao = models.CharField("como agrupar as avaliações", max_length=12, choices=Agregacao.choices,
                                 default=Agregacao.SOMA)
    ordem = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["fase__ordem", "ordem", "id"]
        constraints = [models.UniqueConstraint(fields=["fase", "sigla"], name="uniq_componente_sigla")]

    def __str__(self):
        return f"{self.nome} ({self.sigla})"


class ConfiguracaoDiario(models.Model):
    """Critérios de avaliação e frequência da escola."""

    instituicao = models.OneToOneField("core.Instituicao", on_delete=models.CASCADE, related_name="config_diario")
    media_aprovacao = models.DecimalField("média mínima de aprovação", max_digits=4, decimal_places=2,
                                          default=Decimal("6.00"), validators=[MinValueValidator(0), MaxValueValidator(10)])
    frequencia_minima = models.DecimalField("frequência mínima (%)", max_digits=5, decimal_places=2,
                                            default=Decimal("75.00"), validators=[MinValueValidator(0), MaxValueValidator(100)])
    usa_recuperacao = models.BooleanField("usa recuperação por período", default=True)
    nota_maxima_padrao = models.DecimalField("nota máxima padrão", max_digits=5, decimal_places=2, default=Decimal("10.00"))


class AlocacaoProfessor(models.Model):
    class Situacao(models.TextChoices):
        ATIVA = "ATIVA", "Ativa"
        ENCERRADA = "ENCERRADA", "Encerrada"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    ano_letivo = models.ForeignKey("academic.AnoLetivo", on_delete=models.PROTECT, related_name="alocacoes")
    turma = models.ForeignKey("academic.Turma", on_delete=models.PROTECT, related_name="alocacoes")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT, related_name="alocacoes")
    professor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="alocacoes")
    situacao = models.CharField(max_length=10, choices=Situacao.choices, default=Situacao.ATIVA)
    data_inicio = models.DateField(default=timezone.localdate)
    data_fim = models.DateField(null=True, blank=True)
    criada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["turma__nome", "disciplina__nome"]
        permissions = [("coordenar_diario", "Pode coordenar o diário de classe"),
                       ("gerenciar_alocacoes", "Pode alocar professores em turmas e disciplinas")]
        constraints = [models.UniqueConstraint(fields=["turma", "disciplina", "professor"],
                                               condition=Q(situacao="ATIVA"), name="uniq_alocacao_ativa")]

    def __str__(self):
        return f"{self.professor.get_full_name() or self.professor.perfil.login} — {self.disciplina} — {self.turma}"


class Aula(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "RASCUNHO", "Rascunho"
        FINALIZADA = "FINALIZADA", "Finalizada"
        CORRECAO = "CORRECAO", "Correção solicitada"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="aulas")
    turma = models.ForeignKey("academic.Turma", on_delete=models.PROTECT, related_name="+")
    disciplina = models.ForeignKey(Disciplina, on_delete=models.PROTECT, related_name="+")
    professor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    periodo = models.ForeignKey(PeriodoLetivo, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    data = models.DateField("data da aula")
    horario_inicio = models.TimeField("início", null=True, blank=True)
    horario_fim = models.TimeField("fim", null=True, blank=True)
    quantidade = models.PositiveSmallIntegerField("aulas ministradas", default=1,
                                                  validators=[MinValueValidator(1), MaxValueValidator(10)])
    conteudo = models.TextField("conteúdo programático", blank=True)
    metodologia = models.TextField("metodologia", blank=True)
    atividades = models.TextField("atividades desenvolvidas", blank=True)
    observacoes = models.TextField("observações", blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RASCUNHO)
    motivo_correcao = models.CharField("motivo da correção solicitada", max_length=255, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    finalizada_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-data", "-id"]
        permissions = [("usar_diario_professor", "Pode usar o diário de classe como professor")]
        indexes = [models.Index(fields=["alocacao", "data"])]

    @property
    def editavel(self):
        return self.status != self.Status.FINALIZADA


class Frequencia(models.Model):
    class Situacao(models.TextChoices):
        PRESENTE = "P", "Presente"
        FALTA = "F", "Falta"
        JUSTIFICADA = "J", "Falta justificada"

    INSTITUICAO_DE = "aula"
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+", editable=False)
    aula = models.ForeignKey(Aula, on_delete=models.CASCADE, related_name="frequencias")
    matricula = models.ForeignKey("enrollments.Matricula", on_delete=models.PROTECT, related_name="frequencias")
    situacao = models.CharField(max_length=1, choices=Situacao.choices, default=Situacao.PRESENTE)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["aula", "matricula"], name="uniq_frequencia_aula_matricula")]


class Avaliacao(models.Model):
    class Tipo(models.TextChoices):
        NORMAL = "NORMAL", "Avaliação"
        RECUPERACAO = "RECUPERACAO", "Recuperação da fase"
        REC_FINAL = "REC_FINAL", "Recuperação final"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="avaliacoes")
    periodo = models.ForeignKey(PeriodoLetivo, on_delete=models.PROTECT, related_name="avaliacoes")
    titulo = models.CharField("título", max_length=120)
    tipo = models.CharField(max_length=12, choices=Tipo.choices, default=Tipo.NORMAL)
    componente = models.ForeignKey(ComponenteNota, null=True, blank=True, on_delete=models.PROTECT, related_name="avaliacoes",
                                   verbose_name="componente da nota")
    data = models.DateField("data")
    nota_maxima = models.DecimalField("nota máxima", max_digits=5, decimal_places=2, default=Decimal("10.00"),
                                      validators=[MinValueValidator(Decimal("0.01"))])
    peso = models.DecimalField("peso", max_digits=5, decimal_places=2, default=Decimal("1.00"),
                               validators=[MinValueValidator(Decimal("0.01"))])
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["periodo__ordem", "data", "id"]


class Nota(models.Model):
    INSTITUICAO_DE = "avaliacao"
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+", editable=False)
    avaliacao = models.ForeignKey(Avaliacao, on_delete=models.CASCADE, related_name="notas")
    matricula = models.ForeignKey("enrollments.Matricula", on_delete=models.PROTECT, related_name="notas")
    valor = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                validators=[MinValueValidator(0)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["avaliacao", "matricula"], name="uniq_nota_avaliacao_matricula")]


class Planejamento(models.Model):
    class Tipo(models.TextChoices):
        ANUAL = "ANUAL", "Anual"
        PERIODO = "PERIODO", "Por período"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="planejamentos")
    tipo = models.CharField(max_length=8, choices=Tipo.choices, default=Tipo.ANUAL)
    periodo = models.ForeignKey(PeriodoLetivo, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    conteudos_previstos = models.TextField("conteúdos previstos")
    objetivos = models.TextField("objetivos de aprendizagem", blank=True)
    metodologias = models.TextField("metodologias", blank=True)
    habilidades = models.TextField("habilidades e competências", blank=True)
    bncc = models.CharField("códigos BNCC (ex.: EF05MA01, EF05MA02)", max_length=200, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tipo", "periodo__ordem", "id"]


class FechamentoDiario(models.Model):
    """Fechamento do diário (turma/disciplina) em um período. Fechado: aulas, frequência e notas ficam só leitura."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="fechamentos")
    periodo = models.ForeignKey(PeriodoLetivo, on_delete=models.PROTECT, related_name="fechamentos")
    fechado = models.BooleanField(default=True)
    fechado_em = models.DateTimeField(null=True, blank=True)
    fechado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reaberto_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    motivo_reabertura = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["alocacao", "periodo"], name="uniq_fechamento")]


class ResultadoFase(models.Model):
    """Nota de uma fase liberada ao boletim. Gravada pela rotina `sincronizar_boletim` quando o professor fecha o
    diário da fase; apagada se o diário for reaberto."""

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    matricula = models.ForeignKey("enrollments.Matricula", on_delete=models.PROTECT, related_name="resultados_fase")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="resultados_fase")
    periodo = models.ForeignKey(PeriodoLetivo, on_delete=models.PROTECT, related_name="resultados")
    nota = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    frequencia = models.DecimalField("frequência acumulada (%)", max_digits=5, decimal_places=2, null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["matricula", "alocacao", "periodo"], name="uniq_resultado_fase")]


class ResultadoFinal(models.Model):
    """Resultado do aluno na disciplina, gravado quando todas as fases estão fechadas (base do histórico)."""

    class Resultado(models.TextChoices):
        APROVADO = "APROVADO", "Aprovado"
        REPROVADO = "REPROVADO", "Reprovado"
        PENDENTE = "PENDENTE", "Pendente"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    matricula = models.ForeignKey("enrollments.Matricula", on_delete=models.PROTECT, related_name="resultados_finais")
    alocacao = models.ForeignKey(AlocacaoProfessor, on_delete=models.PROTECT, related_name="resultados_finais")
    media_final = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    frequencia = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    aulas = models.PositiveIntegerField(default=0)
    faltas = models.PositiveIntegerField(default=0)
    situacao = models.CharField(max_length=40)
    resultado = models.CharField(max_length=10, choices=Resultado.choices, default=Resultado.PENDENTE)
    carga_horaria = models.PositiveSmallIntegerField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["matricula", "alocacao"], name="uniq_resultado_final")]
        permissions = [("emitir_boletim", "Pode emitir o boletim escolar"),
                       ("emitir_historico", "Pode emitir o histórico escolar"),
                       ("gerenciar_historico_externo", "Pode cadastrar o histórico de outras escolas")]


class HistoricoExterno(models.Model):
    """Ano cursado em outra escola, cadastrado manualmente para compor o histórico escolar."""

    class Resultado(models.TextChoices):
        APROVADO = "APROVADO", "Aprovado"
        REPROVADO = "REPROVADO", "Reprovado"
        TRANSFERIDO = "TRANSFERIDO", "Transferido"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    aluno = models.ForeignKey("students.Aluno", on_delete=models.PROTECT, related_name="historicos_externos")
    ano = models.PositiveSmallIntegerField("ano", validators=[MinValueValidator(1950), MaxValueValidator(2100)])
    serie = models.CharField("série/etapa", max_length=60)
    escola = models.CharField("escola de origem", max_length=120)
    cidade_uf = models.CharField("cidade/UF", max_length=60, blank=True)
    resultado = models.CharField("resultado", max_length=12, choices=Resultado.choices, default=Resultado.APROVADO)
    observacoes = models.CharField("observações", max_length=255, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ano", "id"]

    def __str__(self):
        return f"{self.serie} — {self.escola} ({self.ano})"


class HistoricoExternoDisciplina(models.Model):
    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="+")
    historico = models.ForeignKey(HistoricoExterno, on_delete=models.CASCADE, related_name="disciplinas")
    nome = models.CharField("disciplina", max_length=80)
    media = models.CharField("média/conceito", max_length=12, blank=True)
    carga_horaria = models.PositiveSmallIntegerField("carga horária", null=True, blank=True,
                                                     validators=[MaxValueValidator(2000)])
    frequencia = models.DecimalField("frequência (%)", max_digits=5, decimal_places=2, null=True, blank=True,
                                     validators=[MinValueValidator(0), MaxValueValidator(100)])

    class Meta:
        ordering = ["id"]
