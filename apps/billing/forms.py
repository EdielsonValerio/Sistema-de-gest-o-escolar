from django import forms

from apps.core.mixins import BaseForm
from apps.students.utils import cnpj_valido, so_digitos

from .models import Ambiente, Provedor
from .providers.cora import validar_credencial

LIMITE_ARQUIVO = 20 * 1024


class ConfiguracaoBancariaForm(BaseForm):
    provedor = forms.ChoiceField(label="Banco / provedor de cobrança", choices=Provedor.choices)
    ambiente = forms.ChoiceField(label="Ambiente", choices=Ambiente.choices,
                                 help_text="Use Teste (sandbox) para o boleto de teste; troque para Produção só com a chave de produção.")
    api_key = forms.CharField(label="Chave de API", required=False, max_length=200,
                              widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                              help_text="Fica criptografada e nunca é exibida de novo. Deixe em branco para manter a atual.")
    client_id = forms.CharField(label="Client ID (Cora)", required=False, max_length=120,
                                help_text="Recebido do Cora ao solicitar a Integração Direta (Conta > Integrações).")
    certificado_arquivo = forms.FileField(label="Certificado (certificate.pem)", required=False,
                                          widget=forms.ClearableFileInput(attrs={"accept": ".pem,.crt,.cer"}))
    chave_arquivo = forms.FileField(label="Chave privada (private-key.key)", required=False,
                                    widget=forms.ClearableFileInput(attrs={"accept": ".key,.pem"}))
    banco_nome = forms.CharField(label="Banco da escola", required=False, max_length=80)
    agencia = forms.CharField(label="Agência", required=False, max_length=20)
    conta = forms.CharField(label="Conta", required=False, max_length=30)
    beneficiario_nome = forms.CharField(label="Beneficiário (razão social)", required=False, max_length=150)
    beneficiario_documento = forms.CharField(label="CNPJ do beneficiário", required=False, max_length=18)
    instrucoes = forms.CharField(label="Instruções no boleto", required=False, max_length=200,
                                 help_text="Ex.: Não receber após 30 dias do vencimento.")

    def __init__(self, *args, cfg=None, **kwargs):
        self.cfg = cfg
        super().__init__(*args, **kwargs)

    def clean_beneficiario_documento(self):
        d = so_digitos(self.cleaned_data.get("beneficiario_documento", ""))
        if d and not (len(d) == 14 and cnpj_valido(d)):
            raise forms.ValidationError("CNPJ inválido.")
        return d

    def clean_api_key(self):
        valor = (self.cleaned_data.get("api_key") or "").strip()
        if valor and any(ch.isspace() for ch in valor):
            raise forms.ValidationError("A chave de API não pode ter espaços.")
        return valor

    @staticmethod
    def _ler(arquivo, campo):
        if arquivo.size > LIMITE_ARQUIVO:
            raise forms.ValidationError("Arquivo grande demais para ser um certificado/chave.")
        try:
            return arquivo.read().decode("utf-8")
        except UnicodeDecodeError:
            raise forms.ValidationError("Arquivo inválido: envie o arquivo original em texto (PEM).") from None

    def clean(self):
        d = super().clean()
        prov = d.get("provedor")
        if prov in (None, Provedor.SIMULADO):
            return d
        if prov == Provedor.CORA:
            tem = self.cfg and self.cfg.provedor == Provedor.CORA
            if not d.get("client_id") and not (tem and self.cfg.client_id):
                self.add_error("client_id", "Informe o Client ID do Cora.")
            cert, chave = d.get("certificado_arquivo"), d.get("chave_arquivo")
            if bool(cert) != bool(chave):
                self.add_error("chave_arquivo" if cert else "certificado_arquivo",
                               "Envie o certificado e a chave privada juntos.")
            elif cert and chave:
                try:
                    cert_pem, chave_pem = self._ler(cert, "certificado"), self._ler(chave, "chave")
                    d["certificado_validade"], d["certificado_digital"] = validar_credencial(cert_pem, chave_pem)
                    d["certificado_pem"], d["chave_privada_pem"] = cert_pem, chave_pem
                except (forms.ValidationError, ValueError) as exc:
                    self.add_error("certificado_arquivo", exc.messages[0] if hasattr(exc, "messages") else str(exc))
            elif not (tem and self.cfg.tem_certificado):
                self.add_error("certificado_arquivo", "Envie o certificado e a chave privada do Cora.")
        elif not d.get("api_key") and not (self.cfg and self.cfg.tem_api_key):
            self.add_error("api_key", "Informe a chave de API do provedor.")
        return d


class BoletoTesteForm(BaseForm):
    pagador_nome = forms.CharField(label="Nome do pagador do teste", max_length=150)
    pagador_documento = forms.CharField(label="CPF ou CNPJ do pagador", max_length=18,
                                        help_text="O banco exige um documento válido. Pode ser o do próprio responsável pela conta.")
    pagador_email = forms.EmailField(label="E-mail do pagador", required=False)
    valor = forms.DecimalField(label="Valor do boleto de teste (R$)", min_value=1, max_digits=8, decimal_places=2,
                               initial="5.00", localize=False,
                               help_text="Valor real e baixo: no ambiente de produção ele será cobrado de verdade.")
