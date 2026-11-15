"""Ativação de conta, recuperação de senha e MFA (TOTP)."""
import io

import qrcode
import qrcode.image.svg
from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.auth import password_validation
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, resolve_url
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic import FormView, TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin
from apps.core.models import Instituicao

from . import emails, seguranca
from .models import MfaDispositivo, PerfilUsuario, TentativaLogin, TokenConta

T = TentativaLogin.Tipo
MFA_PENDENTE = "mfa_pendente"
MSG_RECUPERACAO = ("Se os dados informados correspondem a uma conta com e-mail cadastrado, enviamos um link para "
                   "redefinir a senha. Ele vale por 1 hora.")


# --------------------------------------------------------------------------- recuperação de senha
class RecuperarForm(forms.Form):
    escola = forms.ModelChoiceField(label="Escola", queryset=Instituicao.objects.none(), empty_label="Selecione a escola…")
    usuario = forms.CharField(label="Usuário", max_length=100)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["escola"].queryset = Instituicao.objects.filter(ativa=True).order_by("nome")
        for f in self.fields.values():
            f.widget.attrs.update({"class": "form-select" if isinstance(f.widget, forms.Select) else "form-control"})


class RecuperarSenhaView(FormView):
    template_name = "accounts/recuperar.html"
    form_class = RecuperarForm

    def form_valid(self, form):
        request = self.request
        escola, login = form.cleaned_data["escola"], form.cleaned_data["usuario"].strip()
        k_conta, k_ip = seguranca.chave_conta(escola.pk, login), seguranca.chave_ip(seguranca.ip_do_pedido(request))
        excedeu = (seguranca.bloqueado(T.RECUPERACAO_CONTA, k_conta, T.RECUPERACAO_IP, k_ip,
                                       seguranca.MAX_RECUPERACOES_CONTA, seguranca.MAX_RECUPERACOES_IP))
        seguranca.registrar_falha(T.RECUPERACAO_CONTA, k_conta)  # conta cada pedido (existindo ou não)
        seguranca.registrar_falha(T.RECUPERACAO_IP, k_ip)
        seguranca.evento("RECUPERACAO_PEDIDA", request, escola=escola, detalhe="bloqueado" if excedeu else "")
        if not excedeu:
            perfil = (PerfilUsuario.objects.select_related("user").filter(instituicao=escola, login__iexact=login,
                                                                          user__is_active=True).first())
            if perfil and perfil.user.email and not perfil.user.is_superuser:
                token = seguranca.gerar_token(perfil.user, TokenConta.Finalidade.RECUPERACAO)
                emails.enviar_recuperacao(usuario=perfil.user, token=token, request=request)
        messages.info(request, MSG_RECUPERACAO)  # mesma resposta existindo ou não a conta
        return redirect("accounts:login")


class DefinirSenhaForm(forms.Form):
    senha1 = forms.CharField(label="Nova senha", strip=False, widget=forms.PasswordInput(render_value=False,
                             attrs={"class": "form-control", "autocomplete": "new-password"}))
    senha2 = forms.CharField(label="Confirmar nova senha", strip=False, widget=forms.PasswordInput(render_value=False,
                             attrs={"class": "form-control", "autocomplete": "new-password"}))

    def __init__(self, *a, usuario=None, **kw):
        self.usuario = usuario
        super().__init__(*a, **kw)

    def clean(self):
        d = super().clean()
        s1, s2 = d.get("senha1"), d.get("senha2")
        if s1 and s2:
            if s1 != s2:
                self.add_error("senha2", "As senhas não conferem.")
            else:
                try:
                    password_validation.validate_password(s1, self.usuario)
                except ValidationError as exc:
                    self.add_error("senha1", exc)
        return d


class DefinirSenhaPorTokenView(FormView):
    """Cria/redefine a senha a partir de um token de uso único. Não inicia sessão (a MFA não é contornada)."""

    template_name = "accounts/definir_senha.html"
    form_class = DefinirSenhaForm
    finalidade = TokenConta.Finalidade.RECUPERACAO

    def dispatch(self, request, *args, **kwargs):
        self.token_valor = kwargs["token"]
        self.registro = seguranca.token_valido(self.token_valor, self.finalidade)
        if self.registro is None:
            seguranca.evento("TOKEN_INVALIDO", request, detalhe=self.finalidade)
            return self._invalido()
        return super().dispatch(request, *args, **kwargs)

    def _invalido(self):
        from django.shortcuts import render
        r = render(self.request, self.template_name, {"invalido": True,
                                                      "ativacao": self.finalidade == TokenConta.Finalidade.ATIVACAO})
        r.status_code = 400
        return r

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["usuario"] = self.registro.user
        return kw

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx["ativacao"] = self.finalidade == TokenConta.Finalidade.ATIVACAO
        return ctx

    @transaction.atomic
    def form_valid(self, form):
        t = seguranca.consumir_token(self.token_valor, self.finalidade)
        if t is None:  # usado por outra requisição no meio do caminho
            return self._invalido()
        u = t.user
        u.set_password(form.cleaned_data["senha1"])  # muda o hash: derruba todas as sessões abertas
        u.save(update_fields=["password"])
        perfil = getattr(u, "perfil", None)
        if perfil is not None:
            perfil.deve_trocar_senha = False
            perfil.senha_trocada_em = timezone.now()
            perfil.save(update_fields=["deve_trocar_senha", "senha_trocada_em"])
            registrar(instituicao=perfil.instituicao, usuario=u,
                      acao="CONTA_ATIVADA" if self.finalidade == TokenConta.Finalidade.ATIVACAO else "SENHA_REDEFINIDA",
                      entidade="User", entidade_id=u.pk, descricao=f"Senha definida por link: {perfil.login}")
        seguranca.evento("CONTA_ATIVADA" if self.finalidade == TokenConta.Finalidade.ATIVACAO else "SENHA_REDEFINIDA",
                         self.request, escola=getattr(perfil, "instituicao", None), usuario=u)
        TokenConta.objects.filter(user=u, usado_em__isnull=True).update(usado_em=timezone.now())  # invalida os demais
        emails.avisar_senha_alterada(usuario=u, request=self.request)
        messages.success(self.request, "Senha definida. Entre com o novo acesso.")
        return redirect("accounts:login")


class AtivarContaView(DefinirSenhaPorTokenView):
    finalidade = TokenConta.Finalidade.ATIVACAO


# --------------------------------------------------------------------------- MFA
def _qr_svg(uri):
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
    buf = io.BytesIO()
    img.save(buf)
    return mark_safe(buf.getvalue().decode())  # nosec B308 B703 - SVG gerado pelo sistema a partir do segredo; sem entrada do usuário


def destino_apos_login(request):
    if request.user.is_superuser and getattr(request.user, "perfil", None) is None:
        padrao = resolve_url("core:escola_lista")
    else:
        padrao = resolve_url("core:dashboard")
    return request.session.pop("login_destino", None) or padrao


def _concluir_mfa(request):
    request.session.pop(MFA_PENDENTE, None)
    return redirect(destino_apos_login(request))


class CodigoForm(forms.Form):
    codigo = forms.CharField(label="Código", max_length=20, strip=True,
                             widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "one-time-code",
                                                           "inputmode": "numeric", "autofocus": True}))


class MfaVerificarView(LoginRequiredMixin, FormView):
    """Segunda etapa do login: código do aplicativo autenticador ou código de recuperação."""

    template_name = "accounts/mfa_verificar.html"
    form_class = CodigoForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.session.get(MFA_PENDENTE) != "verificar":
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user, codigo = self.request.user, form.cleaned_data["codigo"]
        k = seguranca.chave_conta(0, f"mfa:{user.pk}")
        if seguranca.bloqueado(T.MFA_CONTA, k, T.MFA_CONTA, None, seguranca.MAX_FALHAS_MFA):
            form.add_error("codigo", "Muitas tentativas. Aguarde alguns minutos.")
            return self.form_invalid(form)
        disp = MfaDispositivo.objects.filter(user=user, confirmado=True).first()
        ok = disp is not None and seguranca.verificar_totp(disp, codigo)
        if not ok and disp is not None and seguranca.usar_codigo_recuperacao(disp, codigo):
            seguranca.evento("MFA_RECUPERACAO_USADA", self.request, usuario=user)
            messages.warning(self.request, "Você usou um código de recuperação. Gere novos códigos assim que possível.")
            ok = True
        if not ok:
            seguranca.registrar_falha(T.MFA_CONTA, k)
            seguranca.evento("MFA_FALHA", self.request, usuario=user)
            form.add_error("codigo", "Código inválido.")
            return self.form_invalid(form)
        seguranca.limpar_falhas(T.MFA_CONTA, k)
        return _concluir_mfa(self.request)


class MfaConfigurarView(LoginRequiredMixin, TemplateView):
    """Cadastro do aplicativo autenticador (QR code) e emissão dos códigos de recuperação (mostrados uma vez)."""

    template_name = "accounts/mfa_configurar.html"

    def _disp(self):
        return MfaDispositivo.objects.filter(user=self.request.user).first()

    def get(self, request, *args, **kwargs):
        disp = self._disp()
        if disp is not None and disp.confirmado:
            return self.render_to_response({"ja_ativa": True, "pendente": request.session.get(MFA_PENDENTE)})
        segredo = seguranca.iniciar_mfa(request.user)
        return self.render_to_response(self._ctx(segredo))

    def _ctx(self, segredo, erro=""):
        conta = getattr(getattr(self.request.user, "perfil", None), "login", None) or self.request.user.username
        return {"segredo": segredo, "qr": _qr_svg(seguranca.uri_otpauth(segredo, conta)), "erro": erro,
                "pendente": self.request.session.get(MFA_PENDENTE)}

    def post(self, request, *args, **kwargs):
        disp = self._disp()
        if disp is None or disp.confirmado:
            return redirect("accounts:mfa_configurar")
        k = seguranca.chave_conta(0, f"mfa:{request.user.pk}")
        if seguranca.bloqueado(T.MFA_CONTA, k, T.MFA_CONTA, None, seguranca.MAX_FALHAS_MFA):
            return self.render_to_response(self._ctx(seguranca._segredo(disp), "Muitas tentativas. Aguarde alguns minutos."))
        codigos = seguranca.confirmar_mfa(disp, request.POST.get("codigo", ""))
        if codigos is None:
            seguranca.registrar_falha(T.MFA_CONTA, k)
            return self.render_to_response(self._ctx(seguranca._segredo(disp), "Código inválido. Confira o horário do celular."))
        seguranca.limpar_falhas(T.MFA_CONTA, k)
        perfil = getattr(request.user, "perfil", None)
        seguranca.evento("MFA_ATIVADA", request, escola=getattr(perfil, "instituicao", None), usuario=request.user)
        if perfil is not None:
            registrar(instituicao=perfil.instituicao, usuario=request.user, acao="MFA_ATIVADA", entidade="User",
                      entidade_id=request.user.pk, descricao=f"MFA ativada: {perfil.login}")
        pendente = request.session.pop(MFA_PENDENTE, None)
        return self.render_to_response({"codigos": codigos, "concluida": True, "pendente": pendente})


class MfaRedefinirView(AcessoMixin, View):
    """O administrador da escola remove a MFA de um usuário que perdeu o aparelho: ele cadastra outra no próximo login."""

    permission_required = "accounts.gerenciar_usuarios"

    def post(self, request, pk):
        u = get_object_or_404(User, pk=pk, perfil__instituicao=self.instituicao, is_superuser=False)
        MfaDispositivo.objects.filter(user=u).delete()
        registrar(instituicao=self.instituicao, usuario=request.user, acao="MFA_REDEFINIDA", entidade="User",
                  entidade_id=u.pk, descricao=f"MFA redefinida: {u.perfil.login}")
        messages.success(request, "MFA removida. A pessoa cadastrará um novo aplicativo no próximo acesso.")
        return redirect("accounts:usuario_lista")
