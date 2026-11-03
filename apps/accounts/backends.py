from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User

from .models import PerfilUsuario


class EscolaBackend(ModelBackend):
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
