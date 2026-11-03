"""Registro dos conectores. Para um banco novo: crie `providers/<banco>.py` e inclua-o em `CONECTORES`."""
from ..models import Provedor as P
from .asaas import Asaas
from .base import ErroProvedor  # noqa: F401
from .simulado import Simulado

CONECTORES = {P.SIMULADO: Simulado, P.ASAAS: Asaas}


def provedor_da(config):
    return CONECTORES[config.provedor](config)
