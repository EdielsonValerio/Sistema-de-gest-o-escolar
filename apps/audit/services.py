"""Registro de auditoria.

`dados` deve conter apenas informações não sensíveis (ids, valores, status). Chaves que
pareçam dados pessoais ou segredos são descartadas por segurança.
"""
from .models import RegistroAuditoria

_PROIBIDAS = ("senha", "password", "cpf", "cnpj", "documento", "email", "telefone", "celular", "endereco",
              "logradouro", "cep", "token", "secret")


def _limpar(dados):
    limpo = {}
    for k, v in (dados or {}).items():
        if any(p in k.lower() for p in _PROIBIDAS):
            continue
        limpo[k] = str(v) if not isinstance(v, (int, bool, type(None), str, list, dict)) else v
    return limpo


def registrar(*, instituicao, usuario, acao, entidade, entidade_id="", aluno_id=None, descricao="", dados=None):
    return RegistroAuditoria.objects.create(
        instituicao=instituicao,
        usuario=usuario if usuario and getattr(usuario, "pk", None) else None,
        usuario_nome=(usuario.get_username() if usuario and getattr(usuario, "pk", None) else ""),
        acao=acao, entidade=entidade, entidade_id=str(entidade_id or ""),
        aluno_id=aluno_id, descricao=descricao[:255], dados=_limpar(dados),
    )
