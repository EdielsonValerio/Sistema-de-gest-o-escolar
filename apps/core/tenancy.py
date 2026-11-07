"""Isolamento por escola: qual escola o usuário autenticado pode acessar."""
from django.core.exceptions import PermissionDenied

SESSAO_ESCOLA = "escola_id"  # escola escolhida no login (0 = administração do sistema)
SESSAO_SUPORTE = "suporte_escola_id"  # escola aberta pelo administrador do sistema ("modo suporte")


def perfil_do_usuario(user):
    """PerfilUsuario do usuário ou None (administrador do sistema não pertence a nenhuma escola)."""
    return getattr(user, "perfil", None)


def instituicao_do_usuario(user):
    """Escola do usuário autenticado.

    Cada usuário pertence a UMA escola. Administradores do sistema (superusuários sem perfil) não têm escola e
    não acessam dados de escolas pelas telas de secretaria/financeiro. Escolas inativas bloqueiam o acesso.
    """
    perfil = perfil_do_usuario(user)
    if perfil is None:
        raise PermissionDenied("Usuário sem escola vinculada.")
    if not perfil.instituicao.ativa:
        raise PermissionDenied("Escola inativa.")
    return perfil.instituicao


def login_do_usuario(user):
    """Login exibido (sem o prefixo interno usado para garantir unicidade global do username)."""
    if user is None:
        return ""
    perfil = perfil_do_usuario(user)
    return perfil.login if perfil is not None and perfil.login else user.get_username()


def em_modo_suporte(user, request):
    """Administrador do sistema (superusuário sem escola) com uma escola aberta para suporte."""
    return bool(user.is_authenticated and user.is_superuser and perfil_do_usuario(user) is None
                and getattr(request, "instituicao", None) is not None)


def instituicao_da_requisicao(request):
    """Escola da requisição: a do perfil do usuário ou, para o administrador do sistema, a escola aberta em modo
    suporte (definida pelo middleware depois de validada). Sem nenhuma das duas, 403."""
    escola = getattr(request, "instituicao", None)
    user = request.user
    if escola is not None and (perfil_do_usuario(user) is not None or (user.is_superuser and user.is_active)):
        return escola
    return instituicao_do_usuario(user)
