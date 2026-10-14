from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class UF(models.TextChoices):
    AC = "AC", "AC"; AL = "AL", "AL"; AP = "AP", "AP"; AM = "AM", "AM"; BA = "BA", "BA"
    CE = "CE", "CE"; DF = "DF", "DF"; ES = "ES", "ES"; GO = "GO", "GO"; MA = "MA", "MA"
    MT = "MT", "MT"; MS = "MS", "MS"; MG = "MG", "MG"; PA = "PA", "PA"; PB = "PB", "PB"
    PR = "PR", "PR"; PE = "PE", "PE"; PI = "PI", "PI"; RJ = "RJ", "RJ"; RN = "RN", "RN"
    RS = "RS", "RS"; RO = "RO", "RO"; RR = "RR", "RR"; SC = "SC", "SC"; SP = "SP", "SP"
    SE = "SE", "SE"; TO = "TO", "TO"


class Responsavel(models.Model):
    """Responsável financeiro: entidade própria; pode responder por vários alunos."""

    class TipoPessoa(models.TextChoices):
        FISICA = "PF", "Pessoa física"
        JURIDICA = "PJ", "Pessoa jurídica"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="responsaveis")
    nome = models.CharField("nome ou razão social", max_length=150)
    tipo_pessoa = models.CharField("tipo de pessoa", max_length=2, choices=TipoPessoa.choices,
                                   default=TipoPessoa.FISICA)
    documento = models.CharField("CPF/CNPJ", max_length=18, blank=True)
    email_cobranca = models.EmailField("e-mail para cobranças", blank=True)
    telefone = models.CharField("telefone", max_length=20, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    logradouro = models.CharField("logradouro", max_length=150, blank=True)
    numero = models.CharField("número", max_length=10, blank=True)
    complemento = models.CharField("complemento", max_length=60, blank=True)
    bairro = models.CharField("bairro", max_length=80, blank=True)
    municipio = models.CharField("município", max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UF.choices, blank=True)
    eh_proprio_aluno = models.BooleanField("é o próprio aluno", default=False)
    ativo = models.BooleanField("ativo", default=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "responsável financeiro"
        verbose_name_plural = "responsáveis financeiros"
        ordering = ["nome"]
        indexes = [models.Index(fields=["instituicao", "nome"]), models.Index(fields=["instituicao", "documento"])]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "documento"], condition=~Q(documento=""),
                                    name="uniq_responsavel_documento"),
        ]

    def __str__(self):
        return self.nome

    @property
    def documento_mascarado(self):
        from .utils import mascarar_documento
        return mascarar_documento(self.documento)


class Aluno(models.Model):
    class Sexo(models.TextChoices):
        FEMININO = "F", "Feminino"
        MASCULINO = "M", "Masculino"
        OUTRO = "O", "Outro"
        NAO_INFORMADO = "N", "Não informado"

    class TipoDocumento(models.TextChoices):
        RG = "RG", "RG"
        CERTIDAO = "CN", "Certidão de nascimento"
        CNH = "CNH", "CNH"
        PASSAPORTE = "PAS", "Passaporte"
        OUTRO = "OUT", "Outro"

    class ResponsavelLegal(models.TextChoices):
        PAIS = "PAIS", "Os pais"
        OUTRO = "OUTRO", "Outro responsável legal"

    instituicao = models.ForeignKey("core.Instituicao", on_delete=models.PROTECT, related_name="alunos")
    codigo = models.CharField("código único", max_length=30, editable=False)

    # Identificação
    nome = models.CharField("nome completo", max_length=150)
    nome_social = models.CharField("nome social", max_length=150, blank=True)
    data_nascimento = models.DateField("data de nascimento")
    sexo = models.CharField("sexo", max_length=1, choices=Sexo.choices, default=Sexo.NAO_INFORMADO)
    cpf = models.CharField("CPF", max_length=14, blank=True)
    tipo_documento = models.CharField("documento de identificação", max_length=3, choices=TipoDocumento.choices,
                                      blank=True)
    numero_documento = models.CharField("número do documento", max_length=30, blank=True)
    nacionalidade = models.CharField("nacionalidade", max_length=60, default="Brasileira")
    naturalidade = models.CharField("naturalidade", max_length=80, blank=True)
    foto = models.ImageField("fotografia", upload_to="alunos/", blank=True)

    # Contato / endereço
    email = models.EmailField("e-mail", blank=True)
    telefone = models.CharField("telefone", max_length=20, blank=True)
    celular = models.CharField("celular", max_length=20, blank=True)
    cep = models.CharField("CEP", max_length=9, blank=True)
    logradouro = models.CharField("logradouro", max_length=150, blank=True)
    numero = models.CharField("número", max_length=10, blank=True)
    complemento = models.CharField("complemento", max_length=60, blank=True)
    bairro = models.CharField("bairro", max_length=80, blank=True)
    municipio = models.CharField("município", max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, choices=UF.choices, blank=True)

    # Filiação
    nome_mae = models.CharField("nome da mãe", max_length=150, blank=True)
    cpf_mae = models.CharField("CPF da mãe", max_length=14, blank=True)
    telefone_mae = models.CharField("telefone da mãe", max_length=20, blank=True)
    nome_pai = models.CharField("nome do pai", max_length=150, blank=True)
    cpf_pai = models.CharField("CPF do pai", max_length=14, blank=True)
    telefone_pai = models.CharField("telefone do pai", max_length=20, blank=True)
    responsavel_legal = models.CharField("responsável legal", max_length=5, choices=ResponsavelLegal.choices,
                                         default=ResponsavelLegal.PAIS)
    nome_responsavel_legal = models.CharField("nome do responsável legal", max_length=150, blank=True)
    cpf_responsavel_legal = models.CharField("CPF do responsável legal", max_length=14, blank=True)
    telefone_responsavel_legal = models.CharField("telefone do responsável legal", max_length=20, blank=True)
    observacoes_filiacao = models.TextField("observações relevantes", blank=True)

    # Administrativo
    ativo = models.BooleanField("situação cadastral ativa", default=True)
    observacoes_internas = models.TextField("observações internas", blank=True)
    criado_em = models.DateTimeField("data do cadastro", default=timezone.now, editable=False)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", editable=False)

    class Meta:
        verbose_name = "aluno"
        verbose_name_plural = "alunos"
        ordering = ["nome"]
        permissions = [("ver_cpf_completo", "Pode ver o CPF completo")]
        indexes = [
            models.Index(fields=["instituicao", "nome"]),
            models.Index(fields=["instituicao", "cpf"]),
            models.Index(fields=["instituicao", "nome_mae"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["instituicao", "codigo"], name="uniq_aluno_codigo"),
            models.UniqueConstraint(fields=["instituicao", "cpf"], condition=~Q(cpf=""), name="uniq_aluno_cpf"),
        ]

    def __str__(self):
        return f"{self.codigo} — {self.nome}"

    @property
    def nome_exibicao(self):
        return self.nome_social or self.nome

    @property
    def idade(self):
        hoje = timezone.localdate()
        n = self.data_nascimento
        return hoje.year - n.year - ((hoje.month, hoje.day) < (n.month, n.day))

    @property
    def cpf_mascarado(self):
        from .utils import mascarar_documento
        return mascarar_documento(self.cpf)

    @property
    def responsavel_financeiro_atual(self):
        v = self.responsaveis_financeiros.filter(fim__isnull=True).select_related("responsavel").first()
        return v.responsavel if v else None


class AlunoResponsavelFinanceiro(models.Model):
    """Histórico da relação aluno × responsável financeiro. Só um vigente por aluno."""

    class Parentesco(models.TextChoices):
        PROPRIO = "PROPRIO", "Próprio aluno"
        PAI = "PAI", "Pai"
        MAE = "MAE", "Mãe"
        OUTRO = "OUTRO", "Outro responsável"

    aluno = models.ForeignKey(Aluno, on_delete=models.PROTECT, related_name="responsaveis_financeiros")
    responsavel = models.ForeignKey(Responsavel, on_delete=models.PROTECT, related_name="vinculos_alunos")
    parentesco = models.CharField("tipo de responsável", max_length=10, choices=Parentesco.choices)
    inicio = models.DateField("início", default=timezone.localdate)
    fim = models.DateField("fim", null=True, blank=True)
    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = "vínculo aluno × responsável financeiro"
        verbose_name_plural = "vínculos aluno × responsável financeiro"
        ordering = ["-inicio", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["aluno"], condition=Q(fim__isnull=True),
                                    name="uniq_responsavel_vigente_por_aluno"),
            models.CheckConstraint(condition=Q(fim__isnull=True) | Q(fim__gte=models.F("inicio")),
                                   name="chk_vinculo_resp_datas"),
        ]

    def __str__(self):
        return f"{self.aluno_id} → {self.responsavel_id} ({self.parentesco})"
