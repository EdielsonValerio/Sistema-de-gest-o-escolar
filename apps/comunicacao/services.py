from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import registrar
from apps.billing.crypto import SegredoIlegivel, decifrar
from apps.enrollments.models import Matricula
from apps.students.models import AlunoResponsavelFinanceiro, Responsavel

from . import whatsapp as wa
from .models import Comunicado, ConfiguracaoWhatsApp, EnvioComunicado

MAX_DESTINATARIOS = 1000
MAX_POR_HORA = 10
LOTE = 25  # por clique/execução: evita pedido web longo


def responsaveis_alvo(instituicao, alvo, *, responsavel=None, turmas=()):
    """Responsáveis ATIVOS com vínculo vigente a aluno com matrícula vigente (sem duplicar quem tem vários filhos)."""
    if alvo == Comunicado.Alvo.RESPONSAVEL:
        return Responsavel.objects.filter(pk=responsavel.pk, instituicao=instituicao, ativo=True) if responsavel else Responsavel.objects.none()
    mats = Matricula.objects.filter(instituicao=instituicao, status__in=Matricula.STATUS_VIGENTES)
    if alvo == Comunicado.Alvo.TURMAS:
        mats = mats.filter(turma__in=[t.pk for t in turmas], turma__instituicao=instituicao)
    alunos = mats.values("aluno_id")
    ids = AlunoResponsavelFinanceiro.objects.filter(instituicao=instituicao, fim__isnull=True,
                                                    aluno_id__in=alunos).values("responsavel_id")
    return Responsavel.objects.filter(pk__in=ids, instituicao=instituicao, ativo=True).order_by("nome")


def previa(instituicao, alvo, **kw):
    """[(responsavel, e164|None)] — quem receberá e quem está sem WhatsApp válido."""
    return [(r, wa.normalizar_telefone(r.telefone)) for r in responsaveis_alvo(instituicao, alvo, **kw)]


def _token(cfg):
    try:
        return decifrar(cfg.token_cifrado)
    except SegredoIlegivel as e:
        raise ValidationError(str(e)) from e


def criar_comunicado(*, instituicao, usuario, alvo, mensagem, responsavel=None, turmas=(), excluidos=()):
    cfg = ConfiguracaoWhatsApp.obter(instituicao)
    if not (cfg.ativo and cfg.completa):
        raise ValidationError("O WhatsApp da escola não está ativo. Configure e ative em Configurações > WhatsApp.")
    mensagem = wa.limpar_parametro(mensagem)
    if not mensagem:
        raise ValidationError("Escreva a mensagem.")
    if Comunicado.objects.filter(instituicao=instituicao, criado_em__gte=timezone.now() - timedelta(hours=1)).count() >= MAX_POR_HORA:
        raise ValidationError("Limite de comunicados por hora atingido. Tente mais tarde.")
    lista = [(r, e) for r, e in previa(instituicao, alvo, responsavel=responsavel, turmas=turmas) if r.pk not in set(excluidos)]
    if not lista:
        raise ValidationError("Nenhum responsável encontrado para este filtro.")
    if len(lista) > MAX_DESTINATARIOS:
        raise ValidationError(f"Máximo de {MAX_DESTINATARIOS} destinatários por comunicado: use turmas.")
    with transaction.atomic():
        c = Comunicado.objects.create(instituicao=instituicao, alvo=alvo, responsavel=responsavel, mensagem=mensagem,
                                      criado_por=usuario)
        if alvo == Comunicado.Alvo.TURMAS:
            c.turmas.set(turmas)
        EnvioComunicado.objects.bulk_create([EnvioComunicado(
            instituicao=instituicao, comunicado=c, responsavel=r, telefone_mascarado=wa.mascarar_telefone(e),
            status=EnvioComunicado.Status.PENDENTE if e else EnvioComunicado.Status.SEM_WHATSAPP,
            erro="" if e else "Telefone ausente ou não é celular com DDD") for r, e in lista])
        registrar(instituicao=instituicao, usuario=usuario, acao="COMUNICADO_CRIADO", entidade="Comunicado", entidade_id=c.pk,
                  descricao=f"Comunicado ({c.get_alvo_display()}) para {len(lista)} responsável(is)")
    return c


def processar(comunicado, limite=LOTE):
    """Envia até `limite` pendentes. Devolve quantos foram tentados. Falha de um não interrompe os demais."""
    cfg = ConfiguracaoWhatsApp.obter(comunicado.instituicao)
    if not (cfg.ativo and cfg.completa):
        raise ValidationError("O WhatsApp da escola está desativado ou incompleto.")
    token = _token(cfg)
    n = 0
    for e in comunicado.envios.filter(status=EnvioComunicado.Status.PENDENTE).select_related("responsavel")[:limite]:
        n += 1
        fone = wa.normalizar_telefone(e.responsavel.telefone)  # telefone atual, não o do momento da prévia
        if not fone:
            e.status, e.erro = EnvioComunicado.Status.SEM_WHATSAPP, "Telefone ausente ou inválido"
        else:
            try:
                e.message_id = wa.enviar_modelo(cfg, token, fone, comunicado.mensagem)
                e.status, e.erro, e.enviado_em = EnvioComunicado.Status.ENVIADO, "", timezone.now()
            except wa.ErroWhatsApp as err:
                e.status, e.erro = EnvioComunicado.Status.FALHA, str(err)[:200]
        e.save(update_fields=["status", "erro", "message_id", "enviado_em"])
    return n
