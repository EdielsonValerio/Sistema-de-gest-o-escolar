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
            # Segundo fator pendente: nada além das telas de MFA, da saída e dos estáticos (não há atalho).
            if request.session.get("mfa_pendente") and not self._rota_mfa(request):
                return redirect("accounts:mfa_verificar" if request.session["mfa_pendente"] == "verificar"
                                else "accounts:mfa_configurar")
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
    def _rota_mfa(request):
        from django.conf import settings
        from django.urls import reverse
        p = request.path
        return p in (reverse("accounts:mfa_verificar"), reverse("accounts:mfa_configurar"), reverse("accounts:logout")) \
            or p.startswith("/" + settings.STATIC_URL.lstrip("/"))

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
        import secrets
        request.csp_nonce = secrets.token_urlsafe(16)
        resposta = self.get_response(request)
        csp = getattr(settings, "CONTENT_SECURITY_POLICY", "").replace("{nonce}", request.csp_nonce)
        if csp and "Content-Security-Policy" not in resposta:
            nome = ("Content-Security-Policy-Report-Only" if getattr(settings, "CSP_REPORT_ONLY", False)
                    else "Content-Security-Policy")
            resposta[nome] = csp
        resposta.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        resposta.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        return resposta


class LimiteTaxaMiddleware:
    """Limite de requisições por minuto na aplicação (defesa contra abuso e negação de serviço de nível de aplicação).

    * Usuário autenticado: contado por usuário (todas as requisições e, à parte, as que alteram dados).
    * Anônimo: contado por IP (usa o IP real quando DJANGO_CONFIAR_PROXY está ligado).
    * Excedeu: HTTP 429 com Retry-After e um evento LIMITE_TAXA por janela. Arquivos estáticos não contam.

    O contador fica no cache do Django. Com o cache padrão (memória do processo) o limite vale por processo do
    gunicorn; para um limite global configure um cache compartilhado (Redis/Memcached). Complementa — não
    substitui — o `limit_req` do Nginx (ver docs/SECURITY_DEPLOYMENT.md).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        if not getattr(settings, "LIMITE_TAXA_ATIVO", True) or request.path.startswith(settings.STATIC_URL):
            return self.get_response(request)
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            sujeito, tetos = f"u{user.pk}", [("req", settings.LIMITE_REQ_USUARIO_MIN)]
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                tetos.append(("post", settings.LIMITE_POST_USUARIO_MIN))
        else:
            from apps.accounts.seguranca import ip_do_pedido
            sujeito, tetos = f"i{ip_do_pedido(request)}", [("anon", settings.LIMITE_REQ_ANONIMO_MIN)]
        for nome, teto in tetos:
            n = self._contar(f"rl:{nome}:{sujeito}")
            if n > teto:
                return self._recusar(request, nome, n == teto + 1)
        return self.get_response(request)

    @staticmethod
    def _contar(chave):
        import time

        from django.core.cache import cache
        janela = int(time.time() // 60)
        k = f"{chave}:{janela}"
        cache.add(k, 0, timeout=90)
        try:
            return cache.incr(k)
        except ValueError:  # expirou entre o add e o incr
            cache.set(k, 1, timeout=90)
            return 1

    @staticmethod
    def _recusar(request, nome, primeira_vez):
        import time

        from django.http import HttpResponse
        if primeira_vez:  # um evento por janela: a própria trilha não vira vetor de enchimento
            try:
                from apps.accounts import seguranca
                u = request.user if request.user.is_authenticated else None
                perfil = getattr(u, "perfil", None) if u else None
                seguranca.evento("LIMITE_TAXA", request, escola=getattr(perfil, "instituicao", None), usuario=u,
                                 detalhe=f"{nome} {request.method} {request.path}"[:255])
            except Exception:  # nunca derrubar a resposta 429 por falha ao registrar
                pass
        r = HttpResponse("Muitas requisições. Aguarde um minuto e tente novamente.", status=429,
                         content_type="text/plain; charset=utf-8")
        r["Retry-After"] = str(60 - int(time.time()) % 60)
        r["Cache-Control"] = "no-store"
        return r
