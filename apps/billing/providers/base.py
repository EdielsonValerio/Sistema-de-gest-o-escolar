"""Contrato comum a todos os bancos/provedores. Um conector novo só precisa implementar `Provedor`."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class ErroProvedor(Exception):
    """Falha ao falar com o banco. A mensagem é segura para exibir ao usuário (nunca contém chaves)."""


@dataclass
class Pagador:
    nome: str
    documento: str  # CPF/CNPJ (só dígitos)
    email: str = ""
    telefone: str = ""
    cep: str = ""
    numero_endereco: str = ""


@dataclass
class Titulo:
    referencia: str  # identificação própria (ex.: "parcela-123"); volta nos avisos do banco
    valor: Decimal
    vencimento: date
    descricao: str
    pagador: Pagador
    multa_percentual: Decimal = Decimal("0")
    juros_mensal_percentual: Decimal = Decimal("0")


@dataclass
class Emitido:
    id_externo: str
    nosso_numero: str = ""
    linha_digitavel: str = ""
    codigo_barras: str = ""
    pix_copia_cola: str = ""
    pix_qrcode: str = ""  # PNG em base64
    url_boleto: str = ""
    url_fatura: str = ""


@dataclass
class Situacao:
    status: str  # PENDENTE | PAGA | CANCELADA
    valor_pago: Decimal | None = None
    data_pagamento: date | None = None


class Provedor:
    """Interface. `config` é a `ConfiguracaoBancaria` da escola."""

    nome = ""

    def __init__(self, config):
        self.config = config

    def verificar(self):
        """Confere credenciais e ambiente. Levanta `ErroProvedor` se algo estiver errado."""
        raise NotImplementedError

    def emitir(self, titulo: Titulo) -> Emitido:
        raise NotImplementedError

    def consultar(self, id_externo: str) -> Situacao:
        raise NotImplementedError

    def cancelar(self, id_externo: str) -> None:
        raise NotImplementedError

    def alterar(self, id_externo: str, *, valor: Decimal, vencimento: date) -> Emitido:
        """Muda valor/vencimento de um boleto ainda não pago e devolve os dados atualizados."""
        raise NotImplementedError
