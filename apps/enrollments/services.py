"""Serviços de matrícula. Toda operação é atômica e auditada."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import registrar
from apps.core.numbering import gerar_numero_matricula

from .models import Matricula

M = Matricula


def _audit(m, usuario, acao, descricao, **extra):
    registrar(instituicao=m.instituicao, usuario=usuario, acao=acao, entidade="Matricula", entidade_id=m.pk,
              aluno_id=m.aluno_id, descricao=descricao,
              dados={"turma_id": m.turma_id, "numero": m.numero, "status": m.status, **extra})


def _validar_turma_e_aluno(aluno, turma, instituicao, data_ingresso):
    from apps.academic.models import Turma

    if aluno.instituicao_id != instituicao.pk or turma.instituicao_id != instituicao.pk:
        raise ValidationError("Aluno e turma devem pertencer à mesma instituição.")
    if not aluno.ativo:
        raise ValidationError("O cadastro do aluno está inativo.")
    if turma.situacao == Turma.Situacao.ENCERRADA:
        raise ValidationError("A turma está encerrada.")
    if turma.tipo == Turma.Tipo.NAO_OFICIAL:
        if not (turma.data_inicio <= data_ingresso <= turma.data_fim):
            raise ValidationError("A data de ingresso está fora do período de realização da turma.")


def _validar_capacidade(turma):
    if turma.capacidade is not None:
        ocupadas = turma.matriculas.select_for_update().filter(status__in=M.STATUS_VIGENTES).count()
        if ocupadas >= turma.capacidade:
            raise ValidationError("A turma atingiu a capacidade máxima de alunos.")


@transaction.atomic
def criar_matricula(*, aluno, turma, usuario, data_ingresso=None, pendente=False, observacoes="",
                    matricula_anterior=None):
    """Vincula o aluno à turma (oficial ou não oficial) e gera o número, se aplicável."""
    from apps.academic.models import Turma

    instituicao = aluno.instituicao
    data_ingresso = data_ingresso or timezone.localdate()
    _validar_turma_e_aluno(aluno, turma, instituicao, data_ingresso)
    oficial = turma.tipo == Turma.Tipo.OFICIAL

    if M.objects.filter(aluno=aluno, turma=turma, status__in=M.STATUS_VIGENTES).exists():
        raise ValidationError("O aluno já possui vínculo vigente com esta turma.")
    if oficial and M.objects.filter(aluno=aluno, ano_letivo=turma.ano_letivo, tipo="OFICIAL",
                                    status__in=M.STATUS_VIGENTES).exists():
        raise ValidationError(
            f"O aluno já possui matrícula oficial vigente em {turma.ano_letivo.ano}. "
            "Use a transferência para trocar de turma.")
    _validar_capacidade(turma)

    try:
        with transaction.atomic():
            m = M.objects.create(
                instituicao=instituicao, aluno=aluno, turma=turma, tipo=turma.tipo,
                ano_letivo=turma.ano_letivo if oficial else None,
                numero=gerar_numero_matricula(instituicao, turma=turma, aluno=aluno),
                status=M.Status.PENDENTE if pendente else M.Status.ATIVA,
                data_ingresso=data_ingresso, observacoes=observacoes, criado_por=usuario,
                matricula_anterior=matricula_anterior)
    except IntegrityError as exc:
        raise ValidationError(
            "Não foi possível registrar o vínculo: o aluno já está matriculado ou o número gerado já existe "
            "(revise o formato de numeração).") from exc
    _audit(m, usuario, "MATRICULA_CRIADA", f"Vínculo criado na turma {turma.nome}")
    return m


def _bloquear(matricula):
    return M.objects.select_for_update().select_related("turma", "aluno", "instituicao").get(pk=matricula.pk)


def _exigir_transicao(m, novo):
    if not m.pode_transitar_para(novo):
        raise ValidationError(
            f"Transição não permitida: {m.get_status_display()} → {M.Status(novo).label}.")


@transaction.atomic
def ativar_matricula(matricula, usuario):
    m = _bloquear(matricula)
    _exigir_transicao(m, M.Status.ATIVA)
    m.status = M.Status.ATIVA
    m.save(update_fields=["status"])
    _audit(m, usuario, "MATRICULA_ATIVADA", "Matrícula ativada")
    return m


@transaction.atomic
def concluir_matricula(matricula, usuario, data=None):
    m = _bloquear(matricula)
    _exigir_transicao(m, M.Status.CONCLUIDA)
    m.status = M.Status.CONCLUIDA
    m.data_saida = data or timezone.localdate()
    m.save(update_fields=["status", "data_saida"])
    _audit(m, usuario, "MATRICULA_CONCLUIDA", "Matrícula concluída")
    return m


@transaction.atomic
def cancelar_matricula(matricula, usuario, justificativa, data=None, cancelar_parcelas_em_aberto=False):
    if not (justificativa or "").strip():
        raise ValidationError("Informe a justificativa do cancelamento.")
    m = _bloquear(matricula)
    _exigir_transicao(m, M.Status.CANCELADA)
    m.status = M.Status.CANCELADA
    m.data_saida = max(data or timezone.localdate(), m.data_ingresso)
    m.save(update_fields=["status", "data_saida"])
    canceladas = 0
    if cancelar_parcelas_em_aberto:
        from apps.finance.services import cancelar_parcelas_da_matricula
        canceladas = cancelar_parcelas_da_matricula(m, usuario, f"Cancelamento da matrícula: {justificativa}")
    _audit(m, usuario, "MATRICULA_CANCELADA", "Matrícula cancelada", justificativa=justificativa,
           parcelas_canceladas=canceladas)
    return m


@transaction.atomic
def transferir_matricula(matricula, nova_turma, usuario, justificativa, data=None):
    """Encerra o vínculo atual (TRANSFERIDA) e cria o novo, preservando o histórico."""
    from apps.academic.models import Turma

    if not (justificativa or "").strip():
        raise ValidationError("Informe a justificativa da transferência.")
    antiga = _bloquear(matricula)
    if antiga.tipo != "OFICIAL":
        raise ValidationError("Somente matrículas oficiais podem ser transferidas.")
    _exigir_transicao(antiga, M.Status.TRANSFERIDA)
    if nova_turma.tipo != Turma.Tipo.OFICIAL or nova_turma.ano_letivo_id != antiga.ano_letivo_id:
        raise ValidationError("A turma de destino deve ser oficial e do mesmo ano letivo.")
    if nova_turma.pk == antiga.turma_id:
        raise ValidationError("A turma de destino deve ser diferente da atual.")
    data = data or timezone.localdate()
    if data < antiga.data_ingresso:
        raise ValidationError("A data da transferência não pode ser anterior à data de ingresso.")

    antiga.status = M.Status.TRANSFERIDA
    antiga.data_saida = data
    antiga.save(update_fields=["status", "data_saida"])
    nova = criar_matricula(aluno=antiga.aluno, turma=nova_turma, usuario=usuario, data_ingresso=data,
                           observacoes=f"Transferência: {justificativa}", matricula_anterior=antiga)
    _audit(antiga, usuario, "MATRICULA_TRANSFERIDA", "Matrícula transferida",
           nova_matricula_id=nova.pk, nova_turma_id=nova_turma.pk, justificativa=justificativa)
    return nova
