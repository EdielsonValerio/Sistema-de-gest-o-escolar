"""Conector Asaas (https://docs.asaas.com). Boleto registrado com QR Code Pix.

* Ambientes: sandbox `https://api-sandbox.asaas.com/v3` e produção `https://api.asaas.com/v3`.
* Autenticação: cabeçalho `access_token` (chaves `$aact_hmlg_…` no sandbox e `$aact_prod_…` em produção).
  O cabeçalho `User-Agent` é obrigatório em contas novas.
* O QR Code Pix no boleto exige uma **chave Pix cadastrada na conta Asaas**.
* No sandbox o QR Code do boleto não é pagável e multa/juros não são aplicados (limitação do Asaas).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal

from ..codigos import linha_para_barras
from .base import Emitido, ErroProvedor, Provedor, Situacao, Titulo

URLS = {"SANDBOX": "https://api-sandbox.asaas.com/v3", "PRODUCAO": "https://api.asaas.com/v3"}
PREFIXOS = {"SANDBOX": "$aact_hmlg_", "PRODUCAO": "$aact_prod_"}
USER_AGENT = "EduNex-GestaoEscolar/1.0"
TIMEOUT = 20

_PAGO = {"RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH"}
_CANCELADO = {"REFUNDED", "DELETED", "REFUND_REQUESTED", "REFUND_IN_PROGRESS"}


def _data(texto):
    try:
        return date.fromisoformat(texto[:10]) if texto else None
    except ValueError:
        return None


class Asaas(Provedor):
    nome = "Asaas"

    # ------------------------------------------------------------------ HTTP
    def _req(self, metodo, caminho, corpo=None):
        url = URLS[self.config.ambiente] + caminho
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(url, data=dados, method=metodo, headers={
            "access_token": self.config.api_key, "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 (URL fixa, https)
                texto = resp.read().decode() or "{}"
        except urllib.error.HTTPError as exc:
            raise ErroProvedor(self._mensagem_http(exc)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ErroProvedor("Não foi possível conectar ao Asaas. Tente novamente em instantes.") from None
        try:
            return json.loads(texto)
        except ValueError:
            raise ErroProvedor("Resposta inesperada do Asaas.") from None

    @staticmethod
    def _mensagem_http(exc):
        if exc.code == 401:
            return "Asaas recusou a chave de API (401). Confira a chave e se ela é do ambiente escolhido."
        if exc.code == 403:
            return "Asaas negou o acesso (403). Confira as permissões da chave de API."
        try:
            erros = json.loads(exc.read().decode()).get("errors") or []
            texto = "; ".join(e.get("description", "") for e in erros if e.get("description"))
        except Exception:  # noqa: BLE001
            texto = ""
        return f"Asaas recusou a operação ({exc.code}): {texto}" if texto else f"Asaas respondeu com erro {exc.code}."

    # ------------------------------------------------------------------ operações
    def verificar(self):
        chave, esperado = self.config.api_key, PREFIXOS[self.config.ambiente]
        if not chave.startswith(esperado):
            raise ErroProvedor(f"A chave de API não parece ser do ambiente escolhido (deveria começar com {esperado}).")
        self._req("GET", "/customers?limit=1")

    def _cliente(self, p):
        doc = re.sub(r"\D", "", p.documento)
        achado = self._req("GET", "/customers?" + urllib.parse.urlencode({"cpfCnpj": doc, "limit": 1}))
        if achado.get("data"):
            return achado["data"][0]["id"]
        corpo = {"name": p.nome, "cpfCnpj": doc, "notificationDisabled": True}
        for campo, valor in (("email", p.email), ("mobilePhone", re.sub(r"\D", "", p.telefone)),
                             ("postalCode", re.sub(r"\D", "", p.cep)), ("addressNumber", p.numero_endereco)):
            if valor:
                corpo[campo] = valor
        return self._req("POST", "/customers", corpo)["id"]

    def _complementos(self, id_externo):
        campo = self._req("GET", f"/payments/{id_externo}/identificationField")
        linha = campo.get("identificationField") or ""
        barras = re.sub(r"\D", "", campo.get("barCode") or "")
        if len(barras) != 44:
            barras = linha_para_barras(linha)
        pix, qr = "", ""
        try:  # sem chave Pix na conta o boleto sai sem QR Code
            q = self._req("GET", f"/payments/{id_externo}/pixQrCode")
            pix, qr = q.get("payload") or "", q.get("encodedImage") or ""
        except ErroProvedor:
            pass
        return linha, barras, campo.get("nossoNumero") or "", pix, qr

    def emitir(self, t: Titulo):
        corpo = {"customer": self._cliente(t.pagador), "billingType": "BOLETO", "value": float(t.valor),
                 "dueDate": t.vencimento.isoformat(), "description": t.descricao[:500],
                 "externalReference": t.referencia, "postalService": False}
        if t.multa_percentual > 0:
            corpo["fine"] = {"value": float(t.multa_percentual)}
        if t.juros_mensal_percentual > 0:
            corpo["interest"] = {"value": float(t.juros_mensal_percentual)}
        pg = self._req("POST", "/payments", corpo)
        linha, barras, nosso, pix, qr = self._complementos(pg["id"])
        return Emitido(id_externo=pg["id"], nosso_numero=nosso or pg.get("nossoNumero") or "",
                       linha_digitavel=linha, codigo_barras=barras, pix_copia_cola=pix, pix_qrcode=qr,
                       url_boleto=pg.get("bankSlipUrl") or "", url_fatura=pg.get("invoiceUrl") or "")

    def consultar(self, id_externo):
        pg = self._req("GET", f"/payments/{id_externo}")
        status = pg.get("status", "")
        if status in _PAGO:
            valor = pg.get("value")
            return Situacao("PAGA", valor_pago=Decimal(str(valor)) if valor is not None else None,
                            data_pagamento=_data(pg.get("paymentDate") or pg.get("confirmedDate")))
        if status in _CANCELADO or pg.get("deleted"):
            return Situacao("CANCELADA")
        return Situacao("PENDENTE")

    def cancelar(self, id_externo):
        self._req("DELETE", f"/payments/{id_externo}")

    def alterar(self, id_externo, *, valor, vencimento):
        pg = self._req("PUT", f"/payments/{id_externo}", {"value": float(valor), "dueDate": vencimento.isoformat()})
        linha, barras, nosso, pix, qr = self._complementos(id_externo)
        return Emitido(id_externo=id_externo, nosso_numero=nosso, linha_digitavel=linha, codigo_barras=barras,
                       pix_copia_cola=pix, pix_qrcode=qr, url_boleto=pg.get("bankSlipUrl") or "",
                       url_fatura=pg.get("invoiceUrl") or "")
