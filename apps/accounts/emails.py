"""E-mail de boas-vindas com os dados do primeiro acesso.

A senha enviada é PROVISÓRIA: o sistema obriga a criar uma senha própria no primeiro login. Mesmo assim, o envio é
opcional e nunca grava a senha em log ou auditoria. Se o e-mail falhar, o cadastro continua e o usuário é avisado.
"""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

log = logging.getLogger(__name__)


def url_de_acesso(request=None):
    base = settings.SITE_URL or (request.build_absolute_uri("/").rstrip("/") if request is not None else "")
    return f"{base}{reverse('accounts:login')}"


def enviar_boas_vindas(*, usuario, senha_provisoria, escola, request=None, redefinicao=False):
    """Devolve True se enviou. Nunca levanta exceção."""
    if not usuario.email:
        return False
    nome = usuario.first_name or usuario.perfil.login
    assunto = ("EduNex — nova senha provisória" if redefinicao else "EduNex — seu acesso ao sistema")
    corpo = (
        f"Olá, {nome}!\n\n"
        + ("Sua senha foi redefinida pelo administrador.\n\n" if redefinicao else
           f"Foi criado um acesso para você no sistema de gestão da escola {escola.nome}.\n\n")
        + f"Endereço: {url_de_acesso(request)}\n"
        f"Escola: {escola.nome}\n"
        f"Usuário: {usuario.perfil.login}\n"
        f"Senha provisória: {senha_provisoria}\n\n"
        "No primeiro acesso o sistema pedirá que você crie a sua própria senha. A senha provisória deixa de "
        "funcionar depois disso. Não compartilhe estes dados e apague este e-mail após o primeiro acesso.\n"
    )
    try:
        return bool(send_mail(assunto, corpo, settings.DEFAULT_FROM_EMAIL, [usuario.email], fail_silently=False))
    except Exception:  # noqa: BLE001 — falha de e-mail não pode derrubar o cadastro
        log.warning("Falha ao enviar e-mail de boas-vindas (usuário %s).", usuario.pk)
        return False
