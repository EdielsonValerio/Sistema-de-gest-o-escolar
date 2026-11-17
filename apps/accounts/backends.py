from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User

from .models import PerfilUsuario


class FiltroModulosMixin:
    """Tira das permissões do usuário de escola as que pertencem a módulos que a escola não contratou.

    Com isso menu, fichas, painéis e `permission_required` deixam de oferecer/aceitar o que não foi contratado, sem
    espalhar verificações pelo código. Superusuário (administrador do sistema) não passa por aqui."""

    def get_all_permissions(self, user_obj, obj=None):
        perms = super().get_all_permissions(user_obj, obj)
        if obj is not None or not getattr(user_obj, "is_active", False):
            return perms
        from apps.core.modulos import permissao_bloqueada
        from apps.core.tenancy import perfil_do_usuario
        perfil = perfil_do_usuario(user_obj)
        if perfil is None:
            return perms
        escola = perfil.instituicao
        return {p for p in perms if not permissao_bloqueada(escola, p)}


class GlobalBackend(FiltroModulosMixin, ModelBackend):
    """Username global (administrador do sistema); mantém o filtro de módulos para qualquer usuário com escola."""


class EscolaBackend(FiltroModulosMixin, ModelBackend):
    """Autentica por (escola, login, senha). O login é único dentro da escola, não no sistema."""

    def authenticate(self, request, escola=None, login=None, password=None, **kwargs):
        if escola is None or not login or password is None:
            return None
        perfil = (PerfilUsuario.objects.select_related("user", "instituicao")
                  .filter(instituicao=escola, login__iexact=login.strip()).first())
        if perfil is None:
            User().set_password(password)  # iguala o tempo de resposta (não revela se o usuário existe)
            return None
        user = perfil.user
        if perfil.instituicao.ativa and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
