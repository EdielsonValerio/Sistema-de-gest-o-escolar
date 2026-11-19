"""Conector Cora — **Integração Direta** (https://developers.cora.com.br). Boleto registrado com QR Code Pix.

* Autenticação: mTLS. O certificado (`certificate.pem`) e a chave privada (`private-key.key`) vão em TODA chamada,
  junto com o token `client_credentials` (válido por 24 h, guardado cifrado no cache). Teste e produção têm
  credenciais diferentes.
* Hosts: teste `matls-clients.api.stage.cora.com.br`, produção `matls-clients.api.cora.com.br`.
* Valores em centavos; mínimo de R$ 5,00 por boleto. O Pix depende de uma chave Pix cadastrada na conta Cora.
* O Cora **não permite editar** valor/vencimento de um boleto: `alterar` emite um novo e cancela o antigo (o id muda).
* O aviso de pagamento (webhook) chega sem corpo e sem assinatura: é só um sinal; a baixa sempre é confirmada
  consultando o boleto na API.
"""
import json
import os
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache

from .. import crypto
from ..codigos import barras_para_linha, qrcode_base64
from .base import Emitido, ErroProvedor, Provedor, Situacao, Titulo

HOSTS = {"SANDBOX": "https://matls-clients.api.stage.cora.com.br", "PRODUCAO": "https://matls-clients.api.cora.com.br"}
TIMEOUT = 20
VALOR_MINIMO = Decimal("5.00")

_PENDENTE = {"OPEN", "LATE", "INITIATED", "IN_PAYMENT", "DRAFT", "RECURRENCE_DRAFT"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ID_BOLETO = re.compile(r"^inv_[A-Za-z0-9_-]{1,80}$")


def validar_credencial(cert_pem, key_pem):
    """Confere o par certificado/chave recebido do Cora. Devolve (validade: date, impressão digital). ValueError em pt-BR."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    try:
        cert = x509.load_pem_x509_certificate(cert_pem.encode())
    except Exception:  # noqa: BLE001
        raise ValueError("O arquivo do certificado não é um PEM válido (certificate.pem).") from None
    try:
        chave = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except TypeError:
        raise ValueError("A chave privada está protegida por senha; use a chave original entregue pelo Cora.") from None
    except Exception:  # noqa: BLE001
        raise ValueError("O arquivo da chave privada não é válido (private-key.key).") from None
    fmt = dict(encoding=serialization.Encoding.DER, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    if cert.public_key().public_bytes(**fmt) != chave.public_key().public_bytes(**fmt):
        raise ValueError("A chave privada não corresponde ao certificado informado.")
    validade = cert.not_valid_after_utc.date() if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.date()
    if validade < date.today():
        raise ValueError(f"O certificado venceu em {validade:%d/%m/%Y}. Gere um novo no Cora.")
    return validade, cert.fingerprint(hashes.SHA256()).hex()


def _contexto_ssl(cert_pem, key_pem):
    """SSLContext com o certificado de cliente. Os arquivos temporários (0600, em memória se possível) somem na hora."""
    base = "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
    with tempfile.TemporaryDirectory(dir=base) as pasta:
        caminhos = []
        for nome, conteudo in (("c.pem", cert_pem), ("k.pem", key_pem)):
            caminho = os.path.join(pasta, nome)
            fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(conteudo)
            caminhos.append(caminho)
        ctx = ssl.create_default_context()
        try:
            ctx.load_cert_chain(*caminhos)
        except (ssl.SSLError, OSError):
            raise ErroProvedor("Certificado ou chave privada do Cora inválidos.") from None
    return ctx


def _centavos(valor):
    return int((Decimal(valor) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _data(texto):
    try:
        return date.fromisoformat(str(texto)[:10]) if texto else None
    except ValueError:
        return None


def _idem(*partes):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "edunex-cora:" + "|".join(str(p) for p in partes)))


class Cora(Provedor):
    nome = "Cora"

    # ------------------------------------------------------------------ HTTP (mTLS + token)
    def _base(self):
        return HOSTS[self.config.ambiente]

    def _abrir(self, req):
        if not hasattr(self, "_opener"):
            ctx = _contexto_ssl(crypto.decifrar(self.config.certificado_cifrado),
                                crypto.decifrar(self.config.chave_privada_cifrada))
            self._opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))
        return self._opener.open(req, timeout=TIMEOUT)

    def _chamar(self, metodo, caminho, corpo=None, *, token=None, idem=None, form=None):
        url = self._base() + caminho
        cabecalhos = {"Accept": "application/json"}
        dados = None
        if form is not None:
            dados = urllib.parse.urlencode(form).encode()
            cabecalhos["Content-Type"] = "application/x-www-form-urlencoded"
        elif corpo is not None:
            dados = json.dumps(corpo).encode()
            cabecalhos["Content-Type"] = "application/json"
        if token:
            cabecalhos["Authorization"] = f"Bearer {token}"
        if idem:
            cabecalhos["Idempotency-Key"] = idem
        req = urllib.request.Request(url, data=dados, method=metodo, headers=cabecalhos)
        try:
            with self._abrir(req) as resp:  # noqa: S310  # nosec B310 (host fixo https)
                texto = resp.read().decode() or "{}"
        except urllib.error.HTTPError as exc:
            raise ErroProvedor(self._mensagem_http(exc)) from None
        except ErroProvedor:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ssl.SSLError):
            raise ErroProvedor("Não foi possível conectar ao Cora. Tente novamente em instantes.") from None
        try:
            return json.loads(texto)
        except ValueError:
            raise ErroProvedor("Resposta inesperada do Cora.") from None

    @staticmethod
    def _mensagem_http(exc):
        if exc.code == 401:
            return ("Cora recusou as credenciais (401). Confira o Client ID, o certificado e a chave, e se são do "
                    "mesmo ambiente (teste ou produção).")
        if exc.code == 403:
            return "Cora negou o acesso (403). Confira se a Integração Direta está ativa na conta."
        if exc.code == 404:
            return "O Cora não encontrou o registro (404)."
        try:
            corpo = json.loads(exc.read().decode())
            texto = corpo.get("message") or "; ".join(str(e.get("message", e)) for e in corpo.get("errors", []))
            codigo = corpo.get("code", "")
        except Exception:  # noqa: BLE001
            texto = codigo = ""
        texto = (texto or "")[:150]
        return f"Cora recusou a operação ({exc.code}{' ' + codigo if codigo else ''}): {texto}" if texto \
            else f"Cora respondeu com erro {exc.code}."

    def _token(self, forcar=False):
        chave = f"cora-token:{self.config.pk}:{self.config.assinatura()[:24]}"
        if not forcar:
            guardado = cache.get(chave)
            if guardado:
                try:
                    return crypto.decifrar(guardado)
                except crypto.SegredoIlegivel:
                    pass
        r = self._chamar("POST", "/token", form={"grant_type": "client_credentials", "client_id": self.config.client_id})
        token = r.get("access_token")
        if not token:
            raise ErroProvedor("O Cora não devolveu o token de acesso.")
        cache.set(chave, crypto.cifrar(token), max(60, int(r.get("expires_in", 3600)) - 600))
        return token

    def _api(self, metodo, caminho, corpo=None, idem=None):
        try:
            return self._chamar(metodo, caminho, corpo, token=self._token(), idem=idem)
        except ErroProvedor as exc:
            if "(401)" not in str(exc):
                raise
        return self._chamar(metodo, caminho, corpo, token=self._token(forcar=True), idem=idem)  # token vencido: 1 retry

    # ------------------------------------------------------------------ conversões
    @staticmethod
    def _emitido(r):
        bs = (r.get("payment_options") or {}).get("bank_slip") or {}
        barras = re.sub(r"\D", "", bs.get("barcode") or "")
        linha = bs.get("digitable") or (barras_para_linha(barras) if len(barras) == 44 else "")
        emv = (r.get("pix") or {}).get("emv") or ""
        return Emitido(id_externo=r["id"], nosso_numero=bs.get("our_number") or "", linha_digitavel=linha,
                       codigo_barras=barras if len(barras) == 44 else "", pix_copia_cola=emv,
                       pix_qrcode=qrcode_base64(emv) if emv else "", url_boleto=bs.get("url") or "")

    def _completar_pix(self, emitido):
        """O código Pix pode não vir na emissão: consulta o boleto uma vez para pegá-lo."""
        if not emitido.pix_copia_cola:
            try:
                novo = self._emitido(self._api("GET", f"/v2/invoices/{emitido.id_externo}"))
            except ErroProvedor:
                return emitido
            emitido.pix_copia_cola, emitido.pix_qrcode = novo.pix_copia_cola, novo.pix_qrcode
        return emitido

    @staticmethod
    def _servico(descricao, valor):
        return {"name": (descricao or "Mensalidade")[:255], "amount": _centavos(valor)}

    @staticmethod
    def _checar_valor(valor):
        if Decimal(valor) < VALOR_MINIMO:
            raise ErroProvedor("O Cora exige boleto de no mínimo R$ 5,00.")

    # ------------------------------------------------------------------ operações
    def verificar(self):
        self._token(forcar=True)
        self._chamar("GET", "/v2/invoices/?perPage=1&page=1", token=self._token())

    def emitir(self, t: Titulo):
        self._checar_valor(t.valor)
        p = t.pagador
        doc = re.sub(r"\D", "", p.documento)
        if len((p.nome or "").strip()) < 3:
            raise ErroProvedor("O nome do pagador precisa ter ao menos 3 letras.")
        cliente = {"name": p.nome.strip()[:255], "document": {"identity": doc, "type": "CPF" if len(doc) == 11 else "CNPJ"}}
        if p.email and _EMAIL.match(p.email):
            cliente["email"] = p.email
        termos = {"due_date": t.vencimento.isoformat()}
        if t.multa_percentual > 0:
            termos["fine"] = {"rate": float(t.multa_percentual)}
        if t.juros_mensal_percentual > 0:
            termos["interest"] = {"rate": float(t.juros_mensal_percentual)}
        corpo = {"code": t.referencia[:64], "customer": cliente, "services": [self._servico(t.descricao, t.valor)],
                 "payment_terms": termos, "payment_forms": ["BANK_SLIP", "PIX"]}
        chave = _idem(self.config.instituicao_id, t.idempotencia or t.referencia, t.valor, t.vencimento)
        return self._completar_pix(self._emitido(self._api("POST", "/v2/invoices/", corpo, idem=chave)))

    def consultar(self, id_externo):
        r = self._api("GET", f"/v2/invoices/{id_externo}")
        status = r.get("status", "")
        if status == "PAID":
            pagos = r.get("payments") or []
            quando = r.get("occurrence_date") or (pagos[-1].get("finalized_at") if pagos else None)
            return Situacao("PAGA", valor_pago=Decimal(int(r.get("total_paid") or 0)) / 100, data_pagamento=_data(quando))
        if status == "CANCELLED":
            return Situacao("CANCELADA")
        return Situacao("PENDENTE")

    def cancelar(self, id_externo):
        try:
            self._api("DELETE", f"/v2/invoices/{id_externo}")
        except ErroProvedor as exc:
            if "REC-0006" in str(exc):
                raise ErroProvedor("O boleto já foi pago no Cora.") from None
            raise

    def alterar(self, id_externo, *, valor, vencimento, multa_percentual=None, juros_mensal_percentual=None):
        """O Cora não edita boleto: emite um novo com os valores atualizados e só então cancela o antigo."""
        self._checar_valor(valor)
        atual = self._api("GET", f"/v2/invoices/{id_externo}")
        if atual.get("status") == "PAID":
            raise ErroProvedor("O boleto já foi pago no Cora; atualize a situação antes de alterar.")
        cliente = {k: v for k, v in (atual.get("customer") or {}).items() if k in ("name", "email", "document", "address")}
        servico = dict((atual.get("services") or [{}])[0])
        servico = {k: servico[k] for k in ("name", "description", "code") if servico.get(k)}
        servico["amount"] = _centavos(valor)
        termos = {"due_date": vencimento.isoformat()}
        antigos = atual.get("payment_terms") or {}
        multa = antigos.get("fine") if multa_percentual is None else ({"rate": float(multa_percentual)} if multa_percentual > 0 else None)
        juros = antigos.get("interest") if juros_mensal_percentual is None else (
            {"rate": float(juros_mensal_percentual)} if juros_mensal_percentual > 0 else None)
        if multa:
            termos["fine"] = {k: v for k, v in multa.items() if k in ("amount", "rate") and v is not None}
        if juros:
            termos["interest"] = {"rate": juros.get("rate")}
        corpo = {"code": atual.get("code") or "", "customer": cliente, "services": [servico], "payment_terms": termos,
                 "payment_forms": ["BANK_SLIP", "PIX"]}
        novo = self._completar_pix(self._emitido(self._api(
            "POST", "/v2/invoices/", corpo,
            idem=_idem(self.config.instituicao_id, "alterar", id_externo, valor, vencimento))))
        try:
            self.cancelar(id_externo)
        except ErroProvedor as exc:
            novo.aviso = (f"Novo boleto emitido, mas o boleto antigo ({id_externo}) não foi cancelado no Cora: {exc} "
                          "Cancele-o manualmente para evitar pagamento em duplicidade.")
        return novo

    # ------------------------------------------------------------------ extras do Cora
    def registrar_webhook(self, url):
        """Cadastra o endpoint de avisos (todos os eventos de boleto). Repetir com a mesma URL não duplica."""
        return self._api("POST", "/endpoints/", {"url": url, "resource": "invoice", "trigger": "*", "active": True,
                                                 "connectionTimeout": 3000, "readTimeout": 5000},
                         idem=_idem(self.config.instituicao_id, "endpoint", url))

    def simular_pagamento(self, id_externo):
        """Só no teste (stage): o Cora paga o boleto inteiro, na data de hoje, sem multa/juros."""
        if self.config.ambiente != "SANDBOX":
            raise ErroProvedor("A simulação de pagamento só existe no ambiente de teste.")
        self._api("POST", "/v2/invoices/pay", {"id": id_externo}, idem=str(uuid.uuid4()))
