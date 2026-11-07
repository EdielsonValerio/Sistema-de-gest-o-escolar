"""Regras de senha provisória e troca no primeiro acesso."""
from django.contrib.auth import update_session_auth_hash
from django.db import transaction
from django.utils import timezone

from apps.audit.services import registrar


def marcar_senha_provisoria(usuario):
    """Obriga o usuário a criar a própria senha no próximo acesso."""
    usuario.perfil.deve_trocar_senha = True
    usuario.perfil.save(update_fields=["deve_trocar_senha"])


@transaction.atomic
def trocar_senha_propria(request, usuario, nova_senha):
    """O próprio usuário define a nova senha. Encerra o estado de senha provisória e mantém a sessão ativa."""
    usuario.set_password(nova_senha)
    usuario.save(update_fields=["password"])
    perfil = getattr(usuario, "perfil", None)
    era_provisoria = bool(perfil and perfil.deve_trocar_senha)
    if perfil is not None:
        perfil.deve_trocar_senha = False
        perfil.senha_trocada_em = timezone.now()
        perfil.save(update_fields=["deve_trocar_senha", "senha_trocada_em"])
        registrar(instituicao=perfil.instituicao, usuario=usuario,
                  acao="SENHA_INICIAL_SUBSTITUIDA" if era_provisoria else "SENHA_ALTERADA_PELO_USUARIO",
                  entidade="User", entidade_id=usuario.pk, descricao=f"Senha trocada pelo usuário {perfil.login}")
    update_session_auth_hash(request, usuario)
    return era_provisoria
