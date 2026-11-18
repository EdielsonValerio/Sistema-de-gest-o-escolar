from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from apps.accounts.forms import LOGIN_RE, validar_login_da_escola, validar_senha_confirmada
from apps.students.utils import cnpj_valido, formatar_cnpj, so_digitos

from .mixins import BootstrapFormMixin, SecoesMixin
from . import modulos
from .models import Instituicao

LIMITE_LOGOTIPO = 2 * 1024 * 1024


class EscolaForm(BootstrapFormMixin, SecoesMixin, forms.ModelForm):
    ia_habilitada = forms.BooleanField(
        label="Habilitar o assistente de IA (assinatura contratada)", required=False,
        help_text="Libera para a escola a tela de integração do assistente de IA. A escola informa a própria chave "
                  "de API e paga o uso direto ao fornecedor.")

    class Meta:
        model = Instituicao
        fields = ["nome", "cnpj", "logotipo", "ativa", "email_contato", "telefone_contato", "cep", "logradouro", "numero",
                  "complemento", "bairro", "municipio", "uf"]
        help_texts = {"logotipo": "PNG, JPG ou WebP, até 2 MB. Aparece no menu do sistema e nos documentos."}

    SECOES = [("Dados da escola", ["nome", "cnpj", "logotipo", "ativa"]),
              ("Contato e endereço (controle interno)", ["email_contato", "telefone_contato", "cep", "logradouro", "numero",
                                                         "complemento", "bairro", "municipio", "uf"]),
              ("Módulos contratados", [f"mod_{c}" for c in modulos.TODOS] + ["ia_habilitada"])]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.students.models import UF
        self.fields["uf"] = forms.ChoiceField(label="UF", required=False, choices=[("", "—")] + list(UF.choices))
        self.fields["cep"].widget.attrs["placeholder"] = "00000-000"
        self.fields["telefone_contato"].widget.attrs["placeholder"] = "(00) 00000-0000"
        atuais = set(self.instance.modulos or []) if self.instance.pk else set()
        for cod, m in modulos.MODULOS.items():
            ajuda = m.descricao + (f" Exige: {', '.join(modulos.MODULOS[e].nome for e in m.exige)}." if m.exige else "")
            self.fields[f"mod_{cod}"] = forms.BooleanField(label=m.nome, required=False, initial=cod in atuais,
                                                           help_text=ajuda)
            self.fields[f"mod_{cod}"].widget.attrs["class"] = "form-check-input"
        if self.instance.pk:
            from apps.assistente.models import ConfiguracaoIA
            self.fields["ia_habilitada"].initial = ConfiguracaoIA.objects.filter(
                instituicao=self.instance, habilitada=True).exists()

    def modulos_escolhidos(self):
        return modulos.normalizar([c for c in modulos.TODOS if self.cleaned_data.get(f"mod_{c}")])

    def clean(self):
        d = super().clean()
        for mod, exigido in modulos.dependencias_faltando(self.modulos_escolhidos()):
            self.add_error(f"mod_{mod}", f"Para habilitar “{modulos.MODULOS[mod].nome}” habilite também "
                                         f"“{modulos.MODULOS[exigido].nome}”.")
        return d

    def save(self, commit=True):
        self.instance.modulos = self.modulos_escolhidos()
        return super().save(commit=commit)

    def clean_nome(self):
        nome = " ".join(self.cleaned_data["nome"].split())
        qs = Instituicao.objects.filter(nome__iexact=nome)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("Já existe uma escola com este nome.")
        return nome

    def clean_telefone_contato(self):
        v = so_digitos(self.cleaned_data.get("telefone_contato", ""))
        if not v:
            return ""
        if len(v) not in (10, 11):
            raise ValidationError("Informe o telefone com DDD (10 ou 11 dígitos).")
        return f"({v[:2]}) {v[2:7]}-{v[7:]}" if len(v) == 11 else f"({v[:2]}) {v[2:6]}-{v[6:]}"

    def clean_cep(self):
        v = so_digitos(self.cleaned_data.get("cep", ""))
        if not v:
            return ""
        if len(v) != 8:
            raise ValidationError("O CEP tem 8 dígitos.")
        return f"{v[:5]}-{v[5:]}"

    def clean_cnpj(self):
        v = self.cleaned_data.get("cnpj", "")
        if not v:
            return ""
        if not cnpj_valido(v):
            raise ValidationError("CNPJ inválido.")
        return formatar_cnpj(so_digitos(v))

    def clean_logotipo(self):
        from .uploads import processar_imagem
        return processar_imagem(self.cleaned_data.get("logotipo"), max_bytes=LIMITE_LOGOTIPO, lado_saida=800,
                                rotulo="O logotipo")


class AdministradorForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    nome = forms.CharField(label="Nome", max_length=150)
    sobrenome = forms.CharField(label="Sobrenome", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    login = forms.CharField(label="Usuário (login)", max_length=100,
                            help_text="É o que o administrador digita ao entrar, depois de escolher a escola.")
    senha1 = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput(render_value=False))
    senha2 = forms.CharField(label="Confirmar senha", strip=False, widget=forms.PasswordInput(render_value=False))
    enviar_email = forms.BooleanField(
        label="Enviar link de ativação por e-mail", required=False,
        help_text="Requer e-mail preenchido e o envio de e-mail configurado. A senha nunca é enviada por e-mail.")

    SECOES = [("Administrador da escola", ["nome", "sobrenome", "email", "login"]),
              ("Senha provisória (será trocada no primeiro acesso)", ["senha1", "senha2", "enviar_email"])]

    def __init__(self, *args, instituicao=None, **kwargs):
        self.instituicao = instituicao  # None no cadastro (a escola ainda não existe)
        super().__init__(*args, **kwargs)

    def clean_login(self):
        login = self.cleaned_data["login"].strip()
        if self.instituicao is not None:
            return validar_login_da_escola(login, self.instituicao)
        if not LOGIN_RE.match(login):
            raise ValidationError("Use de 3 a 100 caracteres: letras, números e . _ @ + - (sem espaços).")
        return login

    def clean(self):
        d = super().clean()
        validar_senha_confirmada(self, d, User(username=d.get("login", ""), first_name=d.get("nome", "")))
        if d.get("enviar_email") and not d.get("email"):
            self.add_error("email", "Informe o e-mail para enviar o acesso.")
        return d


class NovaSenhaForm(BootstrapFormMixin, SecoesMixin, forms.Form):
    senha1 = forms.CharField(label="Nova senha", strip=False, widget=forms.PasswordInput(render_value=False))
    senha2 = forms.CharField(label="Confirmar nova senha", strip=False, widget=forms.PasswordInput(render_value=False))
    enviar_email = forms.BooleanField(label="Enviar link de redefinição por e-mail", required=False,
                                      help_text="A senha nunca é enviada por e-mail: a pessoa recebe um link de uso único.")

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)

    def clean(self):
        d = super().clean()
        validar_senha_confirmada(self, d, self.usuario)
        if d.get("enviar_email") and not self.usuario.email:
            self.add_error("enviar_email", "Este usuário não tem e-mail cadastrado.")
        return d
