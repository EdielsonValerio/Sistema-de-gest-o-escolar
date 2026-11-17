"""Regras do portal: autenticação própria, sessão, convite e escopo de dados do responsável."""
import logging

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts import seguranca
from apps.audit.services import registrar
from apps.students.models import Aluno
from apps.students.utils import cpf_valido, so_digitos

from .models import AcessoAluno, AcessoResponsavel, ConfiguracaoPortal

log = logging.getLogger(__name__)
S_ACESSO, S_ESCOLA, S_MARCA, S_SLUG = "portal_acesso", "portal_escola", "portal_marca", "portal_slug"
ERRO_LOGIN = "CPF ou senha incorretos, ou acesso não liberado. Em caso de dúvida, procure a secretaria da escola."
_HASH_FALSO = None


class _UsuarioFalso:
    """Para os validadores de senha de similaridade (nome/CPF do responsável)."""

    class _meta:  # o validador de similaridade lê o rótulo do campo em user._meta
        @staticmethod
        def get_field(nome):
            from types import SimpleNamespace
            return SimpleNamespace(verbose_name={"username": "CPF", "first_name": "nome"}.get(nome, nome))

    def __init__(self, nome, cpf):
        self.username, self.first_name, self.last_name, self.email = cpf, nome, "", ""


def validar_senha(senha, acesso):
    validate_password(senha, _UsuarioFalso(acesso.responsavel.nome, acesso.login))  # mínimo 12, comuns, similares


# --------------------------------------------------------------------------- gestão (equipe da escola)
def criar_acesso(responsavel, por):
    """Cria o acesso (ainda NÃO liberado e sem senha). Exige CPF válido e escola com o portal configurado."""
    from django.core.exceptions import ValidationError
    cpf = so_digitos(responsavel.documento)
    if len(cpf) != 11 or not cpf_valido(cpf):
        raise ValidationError("O login do portal é o CPF: cadastre um CPF válido para este responsável.")
    if AcessoResponsavel.objects.filter(instituicao=responsavel.instituicao, login=cpf).exists():
        raise ValidationError("Já existe um acesso ao portal com este CPF nesta escola.")
    a = AcessoResponsavel.objects.create(instituicao=responsavel.instituicao, responsavel=responsavel, login=cpf,
                                         criado_por=por)
    sincronizar_alunos(a)
    return a


def sincronizar_alunos(acesso):
    """Inclui os alunos vinculados HOJE ao responsável (financeiro sim, pedagógico não, até a escola decidir).
    Nunca remove nem altera o que a escola já ajustou."""
    for v in acesso.responsavel.vinculos_alunos.filter(fim__isnull=True).select_related("aluno"):
        AcessoAluno.objects.get_or_create(acesso=acesso, aluno=v.aluno, instituicao=acesso.instituicao,
                                          defaults={"financeiro": True, "pedagogico": False})


def url_convite(token, request=None):
    base = settings.SITE_URL or (request.build_absolute_uri("/").rstrip("/") if request is not None else "")
    return f"{base}{reverse('portal:convite', args=[token])}"


def enviar_convite(acesso, request=None):
    """Gera o convite (uso único, 3 dias). Devolve (url, enviado_por_email). A senha nunca vai por e-mail."""
    token = acesso.gerar_convite()
    url = url_convite(token, request)
    email = acesso.responsavel.email_cobranca
    enviado = False
    if email:
        corpo = (f"Olá, {acesso.responsavel.nome}.\n\n{acesso.instituicao.nome} liberou seu acesso ao portal do responsável.\n"
                 f"Crie sua senha pelo link abaixo (vale por 3 dias e só pode ser usado uma vez):\n\n{url}\n\n"
                 "Se você não esperava este e-mail, ignore-o.")
        try:
            enviado = bool(send_mail(f"Acesso ao portal - {acesso.instituicao.nome}", corpo,
                                     settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False))
        except Exception:  # noqa: BLE001 — falha de e-mail não derruba a operação; a escola copia o link
            log.warning("Falha ao enviar convite do portal (acesso %s).", acesso.pk)
    return url, enviado


# --------------------------------------------------------------------------- autenticação
def _chaves(config, cpf, request):
    T = seguranca.Tipo
    return (T.LOGIN_CONTA, seguranca.chave_conta(f"portal{config.instituicao_id}", cpf),
            T.LOGIN_IP, seguranca.chave_ip(seguranca.ip_do_pedido(request)))


def autenticar(request, config, cpf, senha):
    """(acesso|None, bloqueado). Mesma resposta para qualquer falha; limite por CPF e por IP; espera progressiva."""
    global _HASH_FALSO
    cpf = so_digitos(cpf)
    t_c, k_c, t_i, k_i = _chaves(config, cpf, request)
    if seguranca.bloqueado(t_c, k_c, t_i, k_i):
        seguranca.evento("LOGIN_BLOQUEADO", request, escola=config.instituicao, detalhe="Portal: bloqueio temporário")
        return None, True
    a = (AcessoResponsavel.objects.select_related("responsavel", "instituicao")
         .filter(instituicao=config.instituicao, login=cpf).first())
    if _HASH_FALSO is None:
        _HASH_FALSO = make_password("senha-inexistente")
    ok = False
    if a is not None:
        ok = a.conferir_senha(senha)
    else:
        from django.contrib.auth.hashers import check_password
        check_password(senha, _HASH_FALSO)  # gasta o mesmo tempo: não revela se o CPF existe
    if not ok or not pode_entrar(a):
        seguranca.registrar_falha(t_c, k_c)
        seguranca.registrar_falha(t_i, k_i)
        seguranca.evento("LOGIN_FALHA", request, escola=config.instituicao, detalhe="Portal: credenciais/acesso")
        return None, False
    seguranca.limpar_falhas(t_c, k_c)
    return a, False


def pode_entrar(acesso):
    """Vale para o login E para cada requisição: revogar/desativar tem efeito imediato."""
    if acesso is None or not acesso.liberado or not acesso.tem_senha:
        return False
    cfg = ConfiguracaoPortal.objects.filter(instituicao_id=acesso.instituicao_id).first()
    return bool(cfg and cfg.ativo and acesso.instituicao.ativa and acesso.responsavel.ativo
                and "portal" in (acesso.instituicao.modulos or []))


def entrar(request, acesso, slug):
    request.session.cycle_key()  # contra session fixation
    request.session[S_ACESSO], request.session[S_ESCOLA] = acesso.pk, acesso.instituicao_id
    request.session[S_MARCA], request.session[S_SLUG] = acesso.marca_de_sessao, slug
    acesso.ultimo_acesso = timezone.now()
    acesso.save(update_fields=["ultimo_acesso"])
    registrar(instituicao=acesso.instituicao, usuario=None, acao="PORTAL_LOGIN", entidade="AcessoResponsavel",
              entidade_id=acesso.pk, descricao=f"Responsável {acesso.responsavel.nome} entrou no portal")


def sair(request):
    for k in (S_ACESSO, S_ESCOLA, S_MARCA):
        request.session.pop(k, None)
    request.session.cycle_key()


def acesso_da_sessao(request):
    """O acesso válido desta requisição ou None (revalida tudo no banco a cada chamada)."""
    pk = request.session.get(S_ACESSO)
    if not pk:
        return None
    a = AcessoResponsavel.objects.select_related("responsavel", "instituicao").filter(pk=pk).first()
    if (a is None or a.instituicao_id != request.session.get(S_ESCOLA) or not pode_entrar(a)
            or request.session.get(S_MARCA) != a.marca_de_sessao):
        sair(request)
        return None
    return a


# --------------------------------------------------------------------------- escopo de dados (ÚNICO caminho)
def alunos_do_acesso(acesso, *, financeiro=False, pedagogico=False):
    """Alunos que o responsável pode ver. Sem filtro de flag = qualquer um dos dois. Todas as telas do portal
    obtêm o aluno por aqui; nenhum ID vindo do navegador é aceito sem passar por este filtro."""
    qs = AcessoAluno.objects.filter(acesso=acesso, instituicao=acesso.instituicao, aluno__instituicao=acesso.instituicao)
    mods = acesso.instituicao.modulos or []
    if (financeiro and "financeiro" not in mods) or (pedagogico and "diario" not in mods):
        return Aluno.objects.none()  # a seção depende de um módulo que a escola não contratou
    if financeiro:
        qs = qs.filter(financeiro=True)
    if pedagogico:
        qs = qs.filter(pedagogico=True)
    if not financeiro and not pedagogico:
        from django.db.models import Q
        qs = qs.filter(Q(pedagogico=True) | (Q(financeiro=True) if "financeiro" in mods else Q(pk__in=[])))
    return Aluno.objects.filter(pk__in=qs.values("aluno_id"), instituicao=acesso.instituicao).order_by("nome")
