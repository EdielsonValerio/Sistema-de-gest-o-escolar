"""Isolamento por instituição."""
from django.core.exceptions import PermissionDenied

from .models import ConfiguracaoGeral, Instituicao


def instituicao_do_usuario(user):
    """Instituição do usuário autenticado.

    Superusuários criados por `createsuperuser` ainda não têm perfil: ele é criado
    automaticamente, vinculado à primeira instituição (ou a uma nova).
    """
    from apps.accounts.models import PerfilUsuario

    perfil = getattr(user, "perfil", None)
    if perfil is not None:
        return perfil.instituicao
    if user.is_superuser:
        inst = Instituicao.objects.order_by("pk").first() or Instituicao.objects.create(nome="Minha Escola")
        ConfiguracaoGeral.objects.get_or_create(instituicao=inst)
        PerfilUsuario.objects.create(user=user, instituicao=inst)
        return inst
    raise PermissionDenied("Usuário sem instituição vinculada.")
