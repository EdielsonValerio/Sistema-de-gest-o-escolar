"""Cofre de segredos: chaves de API e tokens de webhook são gravados **criptografados** (Fernet).

A chave de criptografia vem de `BILLING_CRYPTO_KEY` (recomendado, gere com `Fernet.generate_key()`). Sem ela,
é derivada de `DJANGO_SECRET_KEY` (funciona, mas trocar a SECRET_KEY exige reinformar as chaves de API).
Nunca registre o valor decifrado em log, auditoria ou mensagem de erro.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class SegredoIlegivel(Exception):
    """O segredo gravado não pôde ser decifrado (chave de criptografia diferente da usada ao gravar)."""


def _fernet():
    chave = os.environ.get("BILLING_CRYPTO_KEY", "").strip()
    if chave:
        return Fernet(chave.encode())
    derivada = hashlib.sha256(("billing-cofre:" + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(derivada))


def cifrar(texto):
    return _fernet().encrypt(texto.encode()).decode() if texto else ""


def decifrar(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SegredoIlegivel("Não foi possível ler o segredo gravado. Informe a chave de API novamente.") from exc


def mascarar(texto):
    """Mostra só o começo e o fim (ex.: `$aact_…x9fA`)."""
    if not texto:
        return ""
    return texto[:6] + "••••••••" + texto[-4:] if len(texto) > 14 else "••••••••"
