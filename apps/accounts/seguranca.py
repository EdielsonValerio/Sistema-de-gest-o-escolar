"""Segurança de contas: limitação de tentativas, tokens de uso único e MFA (TOTP, RFC 6238).

Nada aqui grava senha, token ou segredo em texto puro: contas/IPs viram HMAC, tokens viram SHA-256 e o segredo
do TOTP é cifrado (Fernet, mesma chave do cofre de segredos).
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from apps.audit.models import EventoSeguranca

from .models import MfaDispositivo, TentativaLogin, TokenConta

# --------------------------------------------------------------------------- limites (ajustáveis em settings)
JANELA = timedelta(minutes=getattr(settings, "LOGIN_JANELA_MINUTOS", 15))
MAX_FALHAS_CONTA = getattr(settings, "LOGIN_MAX_FALHAS_CONTA", 5)
MAX_FALHAS_IP = getattr(settings, "LOGIN_MAX_FALHAS_IP", 30)
MAX_RECUPERACOES_CONTA = 3
MAX_RECUPERACOES_IP = 10
MAX_FALHAS_MFA = 5
Tipo = TentativaLogin.Tipo


def ip_do_pedido(request):
    """IP do cliente. Atrás de proxy reverso ligue DJANGO_CONFIAR_PROXY (usa o último X-Forwarded-For, o do seu proxy)."""
    if getattr(settings, "PRE_MATRICULA_CONFIAR_PROXY", False):
        enc = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if enc:
            return enc.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _hmac(valor):
    return hmac.new(settings.SECRET_KEY.encode(), valor.lower().encode(), hashlib.sha256).hexdigest()


def chave_conta(escola_id, login):
    return _hmac(f"conta:{escola_id or 0}:{(login or '').strip()}")


def chave_ip(ip):
    return _hmac(f"ip:{ip}")


def _contagem(tipo, chave, janela=JANELA):
    return TentativaLogin.objects.filter(tipo=tipo, chave=chave, criado_em__gte=timezone.now() - janela).count()


def registrar_falha(tipo, chave):
    TentativaLogin.objects.create(tipo=tipo, chave=chave)


def limpar_falhas(tipo, chave):
    TentativaLogin.objects.filter(tipo=tipo, chave=chave).delete()


def espera_progressiva(falhas, limite):
    """Minutos de bloqueio: dobra a cada série de `limite` falhas (15 -> 30 -> 60 ...), com teto de 4 h.
    O bloqueio é sempre temporário (evita negação de serviço contra uma conta específica)."""
    if falhas < limite:
        return 0
    return min(15 * 2 ** ((falhas // limite) - 1), 240)


def bloqueado(tipo_conta, chave_c, tipo_ip, chave_i, limite_conta=None, limite_ip=None):
    """Devolve True se a conta OU o IP estão bloqueados no momento."""
    limite_conta = limite_conta or MAX_FALHAS_CONTA
    limite_ip = limite_ip or MAX_FALHAS_IP
    for tipo, chave, limite in ((tipo_conta, chave_c, limite_conta), (tipo_ip, chave_i, limite_ip)):
        if chave is None:
            continue
        espera = espera_progressiva(_contagem(tipo, chave, timedelta(hours=4)), limite)
        if espera:
            ultima = (TentativaLogin.objects.filter(tipo=tipo, chave=chave).order_by("-criado_em")
                      .values_list("criado_em", flat=True).first())
            if ultima and timezone.now() < ultima + timedelta(minutes=espera):
                return True
    return False


def evento(tipo, request=None, *, escola=None, usuario=None, detalhe=""):
    """Registra evento de segurança (sem dados sensíveis)."""
    return EventoSeguranca.objects.create(
        tipo=tipo, instituicao=escola, usuario=usuario if getattr(usuario, "pk", None) else None,
        ip=(ip_do_pedido(request) or None) if request is not None else None, detalhe=detalhe[:255])


# --------------------------------------------------------------------------- tokens de uso único
VALIDADE = {TokenConta.Finalidade.ATIVACAO: timedelta(days=3), TokenConta.Finalidade.RECUPERACAO: timedelta(hours=1)}


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


@transaction.atomic
def gerar_token(usuario, finalidade):
    """Cria um token aleatório de 256 bits, invalida os anteriores da mesma finalidade e devolve o token em claro
    (único momento em que ele existe)."""
    TokenConta.objects.filter(user=usuario, finalidade=finalidade, usado_em__isnull=True).update(usado_em=timezone.now())
    token = secrets.token_urlsafe(32)
    TokenConta.objects.create(user=usuario, finalidade=finalidade, token_hash=_hash_token(token),
                              expira_em=timezone.now() + VALIDADE[finalidade])
    return token


def token_valido(token, finalidade):
    """Devolve o TokenConta se ele existe, é da finalidade, não expirou e não foi usado; senão None."""
    if not token or len(token) > 100:
        return None
    t = (TokenConta.objects.select_related("user").filter(token_hash=_hash_token(token), finalidade=finalidade).first())
    if t is None or t.usado_em is not None or t.expira_em < timezone.now() or not t.user.is_active:
        return None
    return t


@transaction.atomic
def consumir_token(token, finalidade):
    """Marca como usado de forma atômica (duas requisições simultâneas não usam o mesmo token)."""
    t = token_valido(token, finalidade)
    if t is None:
        return None
    ok = TokenConta.objects.filter(pk=t.pk, usado_em__isnull=True).update(usado_em=timezone.now())
    return t if ok else None


# --------------------------------------------------------------------------- MFA (TOTP)
PASSO = 30
DIGITOS = 6


def novo_segredo():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _codigo(segredo, passo):
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8))
    h = hmac.new(chave, struct.pack(">Q", passo), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITOS)
    return f"{n:0{DIGITOS}d}"


def uri_otpauth(segredo, conta, emissor="EduNex"):
    return (f"otpauth://totp/{quote(emissor)}:{quote(conta)}?secret={segredo}&issuer={quote(emissor)}"
            f"&algorithm=SHA1&digits={DIGITOS}&period={PASSO}")


def _segredo(dispositivo):
    from apps.billing import crypto
    return crypto.decifrar(dispositivo.segredo_cifrado)


def iniciar_mfa(usuario):
    """Cria (ou reinicia, se ainda não confirmado) o dispositivo e devolve o segredo em claro para o QR code."""
    from apps.billing import crypto
    segredo = novo_segredo()
    MfaDispositivo.objects.update_or_create(
        user=usuario, defaults=dict(segredo_cifrado=crypto.cifrar(segredo), confirmado=False, ultimo_passo=0,
                                    codigos_recuperacao=[], confirmado_em=None))
    return segredo


def verificar_totp(dispositivo, codigo):
    """Aceita o código atual e o vizinho (±30 s). Rejeita reutilização do mesmo intervalo (replay)."""
    codigo = (codigo or "").strip().replace(" ", "")
    if not (codigo.isdigit() and len(codigo) == DIGITOS):
        return False
    segredo = _segredo(dispositivo)
    agora = int(time.time() // PASSO)
    for passo in (agora - 1, agora, agora + 1):
        if hmac.compare_digest(_codigo(segredo, passo), codigo) and passo > dispositivo.ultimo_passo:
            ok = MfaDispositivo.objects.filter(pk=dispositivo.pk, ultimo_passo__lt=passo).update(ultimo_passo=passo)
            return bool(ok)
    return False


def gerar_codigos_recuperacao(dispositivo, quantidade=8):
    """Gera códigos de uso único; grava só o hash. Devolve os códigos em claro (mostrados uma única vez)."""
    codigos = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(quantidade)]
    dispositivo.codigos_recuperacao = [_hash_token(c) for c in codigos]
    dispositivo.save(update_fields=["codigos_recuperacao"])
    return codigos


def usar_codigo_recuperacao(dispositivo, codigo):
    h = _hash_token((codigo or "").strip().lower())
    with transaction.atomic():
        d = MfaDispositivo.objects.select_for_update().get(pk=dispositivo.pk)
        if h in d.codigos_recuperacao:
            d.codigos_recuperacao = [c for c in d.codigos_recuperacao if c != h]
            d.save(update_fields=["codigos_recuperacao"])
            return True
    return False


def confirmar_mfa(dispositivo, codigo):
    if not verificar_totp(dispositivo, codigo):
        return None
    dispositivo.refresh_from_db()
    dispositivo.confirmado, dispositivo.confirmado_em = True, timezone.now()
    dispositivo.save(update_fields=["confirmado", "confirmado_em"])
    return gerar_codigos_recuperacao(dispositivo)


def tem_mfa(usuario):
    return MfaDispositivo.objects.filter(user=usuario, confirmado=True).exists()


def mfa_obrigatoria(usuario):
    """MFA obrigatória: administrador do sistema, Administrador da escola e Financeiro (e quem tem permissões de
    gestão de usuários). Desligável por DJANGO_MFA_OBRIGATORIA=False (não recomendado em produção)."""
    if not getattr(settings, "MFA_OBRIGATORIA", False):
        return False
    if usuario.is_superuser:
        return True
    return usuario.groups.filter(name__in=getattr(settings, "MFA_PERFIS_OBRIGATORIOS", ())).exists()
