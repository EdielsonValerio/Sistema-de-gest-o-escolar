from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect

from .tenancy import SESSAO_ESCOLA, SESSAO_SUPORTE, perfil_do_usuario


class EscolaMiddleware:
    """Mantém a sessão presa à escola escolhida no login.

    * `request.instituicao`: escola do usuário (None para o administrador do sistema).
    * Encerra a sessão se a escola foi inativada ou se a escola da sessão não é a do usuário.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.instituicao = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            perfil = perfil_do_usuario(user)
            if perfil is not None:
                escola = perfil.instituicao
                na_sessao = request.session.get(SESSAO_ESCOLA)
                if not escola.ativa or (na_sessao not in (None, 0) and na_sessao != escola.pk):
                    logout(request)
                    messages.error(request, "Sessão encerrada: escola inativa ou acesso não autorizado.")
                    return redirect("accounts:login")
                request.instituicao = escola
                if perfil.deve_trocar_senha and not self._rota_liberada(request):
                    return redirect("accounts:senha_trocar")
            elif user.is_superuser and user.is_active:
                # Modo suporte: o administrador do sistema abre uma escola (auditado) e passa a ver os dados dela.
                sid = request.session.get(SESSAO_SUPORTE)
                if sid:
                    from .models import Instituicao
                    request.instituicao = Instituicao.objects.filter(pk=sid).first()
                    if request.instituicao is None:
                        request.session.pop(SESSAO_SUPORTE, None)
        return self.get_response(request)

    @staticmethod
    def _rota_liberada(request):
        """Com senha provisória só a troca de senha, a saída e os arquivos estáticos ficam liberados."""
        from django.conf import settings
        from django.urls import reverse
        p = request.path
        return p in (reverse("accounts:senha_trocar"), reverse("accounts:logout")) or p.startswith(settings.STATIC_URL if settings.STATIC_URL.startswith("/") else "/" + settings.STATIC_URL)


class SegurancaMiddleware:
    """Endurecimento HTTP: bloqueia TRACE/TRACK e acrescenta a Content-Security-Policy e demais cabeçalhos.

    A política (CSP) vem de `settings.CONTENT_SECURITY_POLICY`; se `CSP_REPORT_ONLY` estiver ligado, o navegador
    apenas relata violações (útil para validar antes de impor).
    """

    METODOS_BLOQUEADOS = {"TRACE", "TRACK"}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import HttpResponseNotAllowed

        if request.method in self.METODOS_BLOQUEADOS:
            return HttpResponseNotAllowed(["GET", "HEAD", "POST", "OPTIONS"])
        resposta = self.get_response(request)
        csp = getattr(settings, "CONTENT_SECURITY_POLICY", "")
        if csp and "Content-Security-Policy" not in resposta:
            nome = ("Content-Security-Policy-Report-Only" if getattr(settings, "CSP_REPORT_ONLY", False)
                    else "Content-Security-Policy")
            resposta[nome] = csp
        resposta.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resposta.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return resposta
