"""Cadastro de escolas e de seus administradores (operações do administrador do sistema)."""
from django.contrib.auth.models import Group, User
from django.db import transaction

from apps.accounts.models import PerfilUsuario
from apps.audit.services import registrar

from .models import ConfiguracaoGeral


def criar_administrador(instituicao, *, login, nome, sobrenome="", email="", senha, criado_por):
    """Cria o usuário com perfil Administrador, vinculado SOMENTE a esta escola (não é staff nem superusuário)."""
    user = User(username=f"{instituicao.pk}.{login}", first_name=nome, last_name=sobrenome, email=email,
                is_active=True, is_staff=False, is_superuser=False)
    user.set_password(senha)
    user.save()
    # Senha inicial: provisória. No primeiro acesso o administrador é obrigado a criar a própria senha.
    PerfilUsuario.objects.create(user=user, instituicao=instituicao, login=login, deve_trocar_senha=True)
    user.groups.set([Group.objects.get(name="Administrador")])
    registrar(instituicao=instituicao, usuario=criado_por, acao="ADMIN_ESCOLA_CRIADO", entidade="User",
              entidade_id=user.pk, descricao=f"Administrador da escola: {login}", dados={"login": login})
    return user


@transaction.atomic
def cadastrar_escola(form_escola, dados_admin, criado_por):
    """Cria a escola, suas configurações iniciais e o primeiro administrador, tudo ou nada."""
    escola = form_escola.save()
    ConfiguracaoGeral.objects.get_or_create(instituicao=escola)
    registrar(instituicao=escola, usuario=criado_por, acao="ESCOLA_CADASTRADA", entidade="Instituicao",
              entidade_id=escola.pk, descricao=f"Escola cadastrada: {escola.nome}", dados={"ativa": escola.ativa})
    if form_escola.cleaned_data.get("ia_habilitada"):
        from apps.assistente.servicos import habilitar_pelo_sistema
        habilitar_pelo_sistema(escola, True, criado_por)
    admin = criar_administrador(
        escola, login=dados_admin["login"], nome=dados_admin["nome"], sobrenome=dados_admin.get("sobrenome", ""),
        email=dados_admin.get("email", ""), senha=dados_admin["senha1"], criado_por=criado_por)
    return escola, admin


@transaction.atomic
def redefinir_senha(usuario, nova_senha, por):
    """Redefinição feita por outra pessoa: a nova senha é provisória e o usuário terá de trocá-la ao entrar."""
    usuario.set_password(nova_senha)
    usuario.save(update_fields=["password"])
    usuario.perfil.deve_trocar_senha = True
    usuario.perfil.save(update_fields=["deve_trocar_senha"])
    registrar(instituicao=usuario.perfil.instituicao, usuario=por, acao="SENHA_REDEFINIDA", entidade="User",
              entidade_id=usuario.pk, descricao=f"Senha redefinida: {usuario.perfil.login}")
