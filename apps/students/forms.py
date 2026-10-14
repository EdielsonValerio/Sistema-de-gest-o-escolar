from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.mixins import BaseModelForm

from .models import Aluno, AlunoResponsavelFinanceiro as Vinculo, Responsavel, UF
from .utils import cnpj_valido, cpf_valido, formatar_cnpj, formatar_cpf, so_digitos


def _validar_cpf_opcional(valor, rotulo="CPF"):
    valor = (valor or "").strip()
    if not valor:
        return ""
    if not cpf_valido(valor):
        raise ValidationError(f"{rotulo} inválido.")
    return formatar_cpf(valor)


class AlunoForm(BaseModelForm):
    # --- responsável financeiro (não persistido diretamente no aluno)
    resp_tipo = forms.ChoiceField(
        label="Tipo de responsável financeiro", required=False,
        choices=[("", "— definir depois —")] + list(Vinculo.Parentesco.choices))
    resp_existente = forms.ModelChoiceField(
        label="Responsável já cadastrado", queryset=Responsavel.objects.none(), required=False,
        empty_label="— cadastrar novo / usar dados informados —",
        help_text="Uma pessoa pode ser responsável por mais de um aluno.")
    resp_tipo_pessoa = forms.ChoiceField(label="Tipo de pessoa", required=False, choices=Responsavel.TipoPessoa.choices,
                                         initial="PF")
    resp_nome = forms.CharField(label="Nome ou razão social", max_length=150, required=False)
    resp_documento = forms.CharField(label="CPF/CNPJ", max_length=18, required=False)
    resp_email = forms.EmailField(label="E-mail para cobranças", required=False)
    resp_telefone = forms.CharField(label="Telefone", max_length=20, required=False)
    resp_cep = forms.CharField(label="CEP de cobrança", max_length=9, required=False)
    resp_logradouro = forms.CharField(label="Logradouro de cobrança", max_length=150, required=False)
    resp_numero = forms.CharField(label="Número", max_length=10, required=False)
    resp_complemento = forms.CharField(label="Complemento", max_length=60, required=False)
    resp_bairro = forms.CharField(label="Bairro", max_length=80, required=False)
    resp_municipio = forms.CharField(label="Município", max_length=80, required=False)
    resp_uf = forms.ChoiceField(label="UF", required=False, choices=[("", "—")] + list(UF.choices))

    SECOES = [
        ("Identificação", ["nome", "nome_social", "data_nascimento", "sexo", "cpf", "tipo_documento",
                           "numero_documento", "nacionalidade", "naturalidade", "foto"]),
        ("Contato e endereço", ["email", "telefone", "celular", "cep", "logradouro", "numero", "complemento",
                                "bairro", "municipio", "uf"]),
        ("Filiação", ["nome_mae", "cpf_mae", "telefone_mae", "nome_pai", "cpf_pai", "telefone_pai"]),
        ("Responsável legal", ["responsavel_legal", "nome_responsavel_legal", "cpf_responsavel_legal",
                               "telefone_responsavel_legal", "observacoes_filiacao"]),
        ("Responsável financeiro", ["resp_tipo", "resp_existente", "resp_tipo_pessoa", "resp_nome", "resp_documento",
                                    "resp_email", "resp_telefone"]),
        ("Endereço de cobrança (quando necessário)", ["resp_cep", "resp_logradouro", "resp_numero",
                                                       "resp_complemento", "resp_bairro", "resp_municipio", "resp_uf"]),
        ("Informações administrativas", ["ativo", "observacoes_internas"]),
    ]

    class Meta:
        model = Aluno
        exclude = ["instituicao", "codigo", "criado_em", "criado_por"]
        widgets = {
            "observacoes_filiacao": forms.Textarea(attrs={"rows": 2}),
            "observacoes_internas": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resp_existente"].queryset = Responsavel.objects.filter(
            instituicao=self.instituicao, ativo=True).order_by("nome")
        for nome, mascara in (("cpf", "cpf"), ("cpf_mae", "cpf"), ("cpf_pai", "cpf"), ("cpf_responsavel_legal", "cpf"),
                              ("resp_documento", "cpfcnpj"), ("cep", "cep"), ("resp_cep", "cep"),
                              ("telefone", "tel"), ("celular", "tel"), ("telefone_mae", "tel"),
                              ("telefone_pai", "tel"), ("telefone_responsavel_legal", "tel"), ("resp_telefone", "tel")):
            self.fields[nome].widget.attrs["data-mask"] = mascara
        self.fields["foto"].widget.attrs["accept"] = "image/*"
        if self.instance.pk:
            vig = self.instance.responsaveis_financeiros.filter(fim__isnull=True).select_related("responsavel").first()
            if vig:
                self.fields["resp_tipo"].initial = vig.parentesco
                self.fields["resp_existente"].initial = vig.responsavel_id
        self.fields["resp_existente"].help_text += " Para alterar dados de um responsável, use Responsáveis no menu."

    # --- validações
    def clean_data_nascimento(self):
        d = self.cleaned_data["data_nascimento"]
        if d > timezone.localdate():
            raise ValidationError("A data de nascimento não pode ser futura.")
        if d < date(1900, 1, 1):
            raise ValidationError("Data de nascimento inválida.")
        return d

    def clean_cpf(self):
        cpf = _validar_cpf_opcional(self.cleaned_data.get("cpf"))
        if cpf:
            qs = Aluno.objects.filter(instituicao=self.instituicao, cpf=cpf)
            if self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise ValidationError("Já existe um aluno com este CPF.")
        return cpf

    def clean_cpf_mae(self):
        return _validar_cpf_opcional(self.cleaned_data.get("cpf_mae"), "CPF da mãe")

    def clean_cpf_pai(self):
        return _validar_cpf_opcional(self.cleaned_data.get("cpf_pai"), "CPF do pai")

    def clean_cpf_responsavel_legal(self):
        return _validar_cpf_opcional(self.cleaned_data.get("cpf_responsavel_legal"), "CPF do responsável legal")

    def clean_resp_documento(self):
        v = (self.cleaned_data.get("resp_documento") or "").strip()
        if not v:
            return ""
        d = so_digitos(v)
        if len(d) == 11 and cpf_valido(d):
            return formatar_cpf(d)
        if len(d) == 14 and cnpj_valido(d):
            return formatar_cnpj(d)
        raise ValidationError("CPF/CNPJ inválido.")

    def clean(self):
        d = super().clean()
        tipo = d.get("resp_tipo")
        if tipo == Vinculo.Parentesco.PAI and not (d.get("nome_pai") or "").strip():
            self.add_error("resp_tipo", "Informe o nome do pai na seção Filiação.")
        if tipo == Vinculo.Parentesco.MAE and not (d.get("nome_mae") or "").strip():
            self.add_error("resp_tipo", "Informe o nome da mãe na seção Filiação.")
        if tipo == Vinculo.Parentesco.OUTRO and not d.get("resp_existente") and not (d.get("resp_nome") or "").strip():
            self.add_error("resp_nome", "Selecione um responsável cadastrado ou informe o nome.")
        if d.get("resp_existente") and tipo in (Vinculo.Parentesco.PAI, Vinculo.Parentesco.MAE,
                                                Vinculo.Parentesco.PROPRIO):
            d["resp_existente"] = None  # ignorado: o tipo escolhido usa os dados do cadastro do aluno
        return d

    def save(self, commit=True):
        obj = super().save(commit=False)
        if not obj.instituicao_id:
            obj.instituicao = self.instituicao
        if commit:
            obj.save()
        return obj


class ResponsavelForm(BaseModelForm):
    SECOES = [
        ("Identificação", ["nome", "tipo_pessoa", "documento", "email_cobranca", "telefone", "ativo"]),
        ("Endereço de cobrança", ["cep", "logradouro", "numero", "complemento", "bairro", "municipio", "uf"]),
    ]

    class Meta:
        model = Responsavel
        exclude = ["instituicao", "eh_proprio_aluno"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["documento"].widget.attrs["data-mask"] = "cpfcnpj"
        self.fields["cep"].widget.attrs["data-mask"] = "cep"
        self.fields["telefone"].widget.attrs["data-mask"] = "tel"

    def clean_documento(self):
        v = (self.cleaned_data.get("documento") or "").strip()
        if not v:
            return ""
        d = so_digitos(v)
        tipo = self.data.get(self.add_prefix("tipo_pessoa"), "PF")
        if tipo == "PJ":
            if not cnpj_valido(d):
                raise ValidationError("CNPJ inválido.")
            v = formatar_cnpj(d)
        else:
            if not cpf_valido(d):
                raise ValidationError("CPF inválido.")
            v = formatar_cpf(d)
        qs = Responsavel.objects.filter(instituicao=self.instituicao, documento=v)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Já existe um responsável com este documento.")
        return v
