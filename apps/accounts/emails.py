"""E-mails de conta. **Nunca** enviam senha: o usuário recebe um link de uso único (com prazo) para criar a própria.

O token só existe em claro no e-mail (e na mensagem exibida ao administrador quando o e-mail falha); no banco fica
apenas o hash. Se o envio falhar, o cadastro continua e o administrador é avisado. Nada é gravado em log com o token.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

log = logging.getLogger(__name__)


def _base(request=None):
    return settings.SITE_URL or (request.build_absolute_uri("/").rstrip("/") if request is not None else "")


def url_de_acesso(request=None):
    return f"{_base(request)}{reverse('accounts:login')}"


def url_ativacao(token, request=None):
    return f"{_base(request)}{reverse('accounts:ativar', args=[token])}"


def url_redefinicao(token, request=None):
    return f"{_base(request)}{reverse('accounts:redefinir', args=[token])}"


def _enviar(assunto, corpo, usuario):
    if not usuario.email:
        return False
    try:
        return bool(send_mail(assunto, corpo, settings.DEFAULT_FROM_EMAIL, [usuario.email], fail_silently=False))
    except Exception:  # noqa: BLE001 — falha de e-mail não pode derrubar o cadastro
        log.warning("Falha ao enviar e-mail de conta (usuário %s).", usuario.pk)
        return False


def enviar_ativacao(*, usuario, token, escola, request=None, redefinicao=False):
    """Link para o usuário criar a própria senha (conta nova ou senha redefinida pelo administrador)."""
    nome = usuario.first_name or usuario.perfil.login
    assunto = "EduNex — redefina a sua senha" if redefinicao else "EduNex — ative o seu acesso"
    corpo = (
        f"Olá, {nome}!\n\n"
        + ("O administrador pediu que você crie uma nova senha.\n\n" if redefinicao else
           f"Foi criado um acesso para você no sistema de gestão da escola {escola.nome}.\n\n")
        + f"Escola: {escola.nome}\nUsuário: {usuario.perfil.login}\n\n"
        f"Para criar a sua senha, abra o link abaixo (vale por 3 dias e só pode ser usado uma vez):\n"
        f"{url_ativacao(token, request)}\n\n"
        f"Depois, entre em {url_de_acesso(request)}.\n"
        "Se você não esperava este e-mail, ignore-o. Nunca compartilhe este link.\n"
    )
    return _enviar(assunto, corpo, usuario)


def enviar_recuperacao(*, usuario, token, request=None):
    corpo = (
        f"Olá, {usuario.first_name or usuario.perfil.login}!\n\n"
        "Recebemos um pedido para redefinir a sua senha no EduNex.\n"
        f"Abra o link abaixo (vale por 1 hora e só pode ser usado uma vez):\n{url_redefinicao(token, request)}\n\n"
        "Se você não fez este pedido, ignore este e-mail: a sua senha continua a mesma.\n"
    )
    return _enviar("EduNex — redefinição de senha", corpo, usuario)


def avisar_senha_alterada(*, usuario, request=None):
    corpo = (
        f"Olá, {usuario.first_name or usuario.perfil.login}!\n\n"
        "A senha da sua conta no EduNex acabou de ser alterada. Se foi você, não é preciso fazer nada.\n"
        f"Se não foi, redefina a senha imediatamente em {url_de_acesso(request)} e avise o administrador da escola.\n"
    )
    return _enviar("EduNex — sua senha foi alterada", corpo, usuario)
