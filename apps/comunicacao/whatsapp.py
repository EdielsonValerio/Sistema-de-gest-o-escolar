"""Cliente mínimo da WhatsApp Cloud API (Meta). Host fixo (sem SSRF), timeout curto, o token nunca vai para log/erro."""
import json
import re
import urllib.error
import urllib.request

HOST = "https://graph.facebook.com"
TIMEOUT = 12


class ErroWhatsApp(Exception):
    """Mensagem já segura para mostrar/gravar (sem segredos)."""


def normalizar_telefone(valor):
    """E.164 brasileiro de celular (55 + DDD + 9 dígitos) ou None se não servir para WhatsApp."""
    d = re.sub(r"\D", "", valor or "")
    d = d.lstrip("0") if not d.startswith("55") else d
    if len(d) in (10, 11):
        d = "55" + d
    if not (len(d) == 13 and d.startswith("55")):
        return None
    ddd, resto = d[2:4], d[4:]
    if not ("11" <= ddd <= "99") or resto[0] != "9":
        return None
    return d


def mascarar_telefone(e164):
    return f"+{e164[:2]} {e164[2:4]} •••••{e164[-4:]}" if e164 else ""


def limpar_parametro(texto):
    """A Meta recusa quebras de linha, tabulações e mais de 4 espaços seguidos em variáveis de modelo."""
    return re.sub(r"\s+", " ", texto or "").strip()


def _chamar(cfg, token, caminho, corpo=None):
    url = f"{HOST}/{cfg.versao_api}/{caminho}"
    req = urllib.request.Request(url, data=json.dumps(corpo).encode() if corpo is not None else None,
                                 method="POST" if corpo is not None else "GET",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 — host fixo https
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise ErroWhatsApp(f"A Meta recusou ({e.code}): {msg[:150] or 'sem detalhe'}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise ErroWhatsApp("Não foi possível falar com a Meta (rede ou tempo esgotado).") from None


def testar_conexao(cfg, token):
    r = _chamar(cfg, token, f"{cfg.phone_number_id}?fields=display_phone_number,verified_name")
    return r.get("display_phone_number", ""), r.get("verified_name", "")


def enviar_modelo(cfg, token, e164, texto):
    """Envia o modelo aprovado com `texto` na variável {{1}}. Devolve o id da mensagem."""
    corpo = {"messaging_product": "whatsapp", "to": e164, "type": "template",
             "template": {"name": cfg.template_nome, "language": {"code": cfg.template_idioma},
                          "components": [{"type": "body", "parameters": [{"type": "text", "text": limpar_parametro(texto)}]}]}}
    r = _chamar(cfg, token, f"{cfg.phone_number_id}/messages", corpo)
    return (r.get("messages") or [{}])[0].get("id", "")
