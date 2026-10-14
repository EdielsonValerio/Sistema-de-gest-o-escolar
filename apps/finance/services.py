"""Serviços financeiros: geração de parcelas, ajustes, recebimentos e estornos.

Regras críticas ficam aqui (e em constraints de banco), nunca em views/templates.
Valores monetários usam Decimal com arredondamento ROUND_HALF_UP; nunca float.
"""
import calendar
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import registrar
from apps.core.numbering import gerar_numero_recebimento

from .models import (ZERO, AjusteParcela, AlocacaoRecebimento, ContratoFinanceiro, Estorno, Parcela,
                     PlanoFinanceiro, Recebimento)

CENTAVO = Decimal("0.01")


def q2(valor):
    try:
        return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Valor monetário inválido.")


def parse_valor(valor):
    """Converte texto/Decimal para Decimal com no máximo 2 casas (sem arredondar silenciosamente)."""
    if isinstance(valor, str):
        valor = valor.strip().replace("R$", "").strip()
        if "," in valor:  # formato brasileiro 1.234,56
            valor = valor.replace(".", "").replace(",", ".")
    try:
        d = Decimal(valor)
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Valor monetário inválido.")
    if not d.is_finite():
        raise ValidationError("Valor monetário inválido.")
    if d != d.quantize(CENTAVO):
        raise ValidationError("Use no máximo duas casas decimais.")
    return d.quantize(CENTAVO)


def somar_meses(base, meses, dia):
    """Data no mês (base + meses) com o dia pedido, limitado ao último dia do mês."""
    total = base.year * 12 + (base.month - 1) + meses
    ano, mes = divmod(total, 12)
    mes += 1
    return date(ano, mes, min(dia, calendar.monthrange(ano, mes)[1]))


def calcular_cronograma(*, valor_total, quantidade, data_primeira, dia_vencimento, desconto_percentual=ZERO):
    """Prévia/geração das parcelas. A diferença de centavos fica na última parcela."""
    valor_total = q2(valor_total)
    base = (valor_total / quantidade).quantize(CENTAVO, rounding=ROUND_DOWN)
    itens = []
    acumulado = ZERO
    for n in range(1, quantidade + 1):
        original = base if n < quantidade else valor_total - acumulado
        acumulado += original
        desconto = q2(original * Decimal(desconto_percentual) / Decimal(100))
        vencimento = data_primeira if n == 1 else somar_meses(data_primeira, n - 1, dia_vencimento)
        itens.append({"numero": n, "vencimento": vencimento, "valor_original": original,
                      "desconto": desconto, "valor_devido": original - desconto})
    return itens



def cronograma_do_plano(plano):
    return calcular_cronograma(
        valor_total=plano.valor_total, quantidade=plano.quantidade_parcelas,
        data_primeira=plano.data_primeira_parcela, dia_vencimento=plano.dia_vencimento,
        desconto_percentual=plano.desconto_padrao_percentual)


# --------------------------------------------------------------------------- status

def recalcular_status(parcela):
    """Atualiza situação/quitação a partir de valor_pago e valor_devido."""
    if parcela.status == Parcela.Status.CANCELADA:
        return
    if parcela.valor_pago >= parcela.valor_devido:
        parcela.status = Parcela.Status.QUITADA
        parcela.quitacao = parcela.quitacao or timezone.localdate()
    elif parcela.valor_pago > 0:
        parcela.status = Parcela.Status.PARCIAL
        parcela.quitacao = None
    else:
        parcela.status = Parcela.Status.ABERTA
        parcela.quitacao = None


# --------------------------------------------------------------------------- contratação

@transaction.atomic
def contratar_plano(*, matricula, plano, responsavel, usuario):
    """Gera as parcelas da matrícula a partir do plano. Idempotente: uma matrícula tem um único contrato."""
    from apps.enrollments.models import Matricula

    mat = Matricula.objects.select_for_update().select_related("aluno", "instituicao").get(pk=matricula.pk)
    inst = mat.instituicao
    if plano.instituicao_id != inst.pk or responsavel.instituicao_id != inst.pk:
        raise ValidationError("Plano ou responsável de outra instituição.")
    if plano.situacao != PlanoFinanceiro.Situacao.ATIVO:
        raise ValidationError("O plano financeiro não está ativo.")
    if plano.turma_id != mat.turma_id:
        raise ValidationError("O plano não pertence à turma da matrícula.")
    if mat.status not in (Matricula.Status.PENDENTE, Matricula.Status.ATIVA):
        raise ValidationError("A matrícula não está vigente.")
    if not responsavel.ativo:
        raise ValidationError("O responsável financeiro está inativo.")
    if ContratoFinanceiro.objects.filter(matricula=mat).exists():
        raise ValidationError("Esta matrícula já possui cobranças geradas.")

    try:
        with transaction.atomic():
            contrato = ContratoFinanceiro.objects.create(
                instituicao=inst, matricula=mat, plano=plano, responsavel=responsavel, plano_nome=plano.nome,
                valor_total=plano.valor_total, quantidade_parcelas=plano.quantidade_parcelas,
                desconto_padrao_percentual=plano.desconto_padrao_percentual,
                aplicar_encargos=plano.aplicar_encargos, multa_percentual=plano.multa_percentual,
                juros_mensal_percentual=plano.juros_mensal_percentual, criado_por=usuario)
    except IntegrityError as exc:
        raise ValidationError("Esta matrícula já possui cobranças geradas.") from exc

    hoje = timezone.localdate()
    parcelas = []
    for it in cronograma_do_plano(plano):
        parcelas.append(Parcela(
            instituicao=inst, aluno=mat.aluno, matricula=mat, contrato=contrato, responsavel=responsavel,
            numero=it["numero"], descricao=f"{plano.nome} — parcela {it['numero']}/{plano.quantidade_parcelas}",
            valor_original=it["valor_original"], desconto=it["desconto"], acrescimo=ZERO,
            valor_devido=it["valor_devido"], emissao=hoje, vencimento=it["vencimento"]))
    Parcela.objects.bulk_create(parcelas)
    registrar(instituicao=inst, usuario=usuario, acao="PARCELAS_GERADAS", entidade="ContratoFinanceiro",
              entidade_id=contrato.pk, aluno_id=mat.aluno_id, descricao=f"{len(parcelas)} parcelas geradas",
              dados={"plano_id": plano.pk, "matricula_id": mat.pk, "responsavel_id": responsavel.pk,
                     "quantidade": len(parcelas), "valor_total": plano.valor_total})
    return contrato


@transaction.atomic
def matricular_com_cobranca(*, aluno, turma, usuario, plano=None, responsavel=None, data_ingresso=None,
                            sem_cobranca=False):
    """Fluxo completo: cria a matrícula e (se houver plano) as parcelas, em uma única transação."""
    from apps.enrollments.services import criar_matricula

    from apps.core.models import ConfiguracaoGeral

    planos = PlanoFinanceiro.objects.filter(turma=turma, situacao=PlanoFinanceiro.Situacao.ATIVO)
    if plano is None and planos.exists():
        exigir = ConfiguracaoGeral.objects.filter(instituicao=turma.instituicao_id,
                                                  exigir_plano_na_matricula=True).exists()
        if exigir:
            raise ValidationError("A instituição exige a escolha de um plano financeiro nesta turma.")
        if not sem_cobranca:
            raise ValidationError("Esta turma possui planos financeiros ativos: escolha um plano ou "
                                  "conclua a matrícula sem cobranças (exige permissão).")
    if plano is not None and responsavel is None:
        raise ValidationError("Informe o responsável financeiro.")
    matricula = criar_matricula(aluno=aluno, turma=turma, usuario=usuario, data_ingresso=data_ingresso)
    contrato = None
    if plano is not None:
        contrato = contratar_plano(matricula=matricula, plano=plano, responsavel=responsavel, usuario=usuario)
    return matricula, contrato


# --------------------------------------------------------------------------- ajustes

def _exigir_justificativa(texto):
    texto = (texto or "").strip()
    if not texto:
        raise ValidationError("Informe a justificativa.")
    return texto[:255]


def _travar(parcela):
    return Parcela.objects.select_for_update().select_related("instituicao").get(pk=parcela.pk)


def _registrar_ajuste(p, usuario, tipo, anterior, posterior, devido_antes, justificativa):
    AjusteParcela.objects.create(
        parcela=p, tipo=tipo, anterior=str(anterior), posterior=str(posterior),
        valor_devido_anterior=devido_antes, valor_devido_posterior=p.valor_devido,
        justificativa=justificativa, usuario=usuario)
    registrar(instituicao=p.instituicao, usuario=usuario, acao=f"PARCELA_{tipo}", entidade="Parcela",
              entidade_id=p.pk, aluno_id=p.aluno_id, descricao=justificativa,
              dados={"anterior": str(anterior), "posterior": str(posterior),
                     "valor_devido": p.valor_devido, "parcela": p.numero})


def _exigir_editavel(p):
    if p.status in (Parcela.Status.QUITADA, Parcela.Status.CANCELADA):
        raise ValidationError("Parcela quitada ou cancelada não pode ser alterada.")


def _persistir_valores(p):
    p.recalcular_valor_devido()
    if p.valor_devido < p.valor_pago:
        raise ValidationError("O novo valor ficaria abaixo do que já foi pago. Estorne o recebimento antes.")
    recalcular_status(p)
    p.save()


@transaction.atomic
def aplicar_desconto(parcela, novo_desconto, usuario, justificativa):
    """Define o desconto total da parcela (valor absoluto)."""
    justificativa = _exigir_justificativa(justificativa)
    novo = parse_valor(novo_desconto)
    p = _travar(parcela)
    _exigir_editavel(p)
    if novo < 0 or novo > p.valor_original:
        raise ValidationError("O desconto deve estar entre zero e o valor original.")
    anterior, devido_antes = p.desconto, p.valor_devido
    p.desconto = novo
    _persistir_valores(p)
    _registrar_ajuste(p, usuario, "DESCONTO", anterior, novo, devido_antes, justificativa)
    return p


@transaction.atomic
def alterar_vencimento(parcela, nova_data, usuario, justificativa):
    justificativa = _exigir_justificativa(justificativa)
    p = _travar(parcela)
    _exigir_editavel(p)
    anterior = p.vencimento
    p.vencimento = nova_data
    p.save(update_fields=["vencimento"])
    _registrar_ajuste(p, usuario, "VENCIMENTO", anterior.isoformat(), nova_data.isoformat(), p.valor_devido,
                      justificativa)
    return p


@transaction.atomic
def corrigir_valor(parcela, novo_valor_original, usuario, justificativa):
    justificativa = _exigir_justificativa(justificativa)
    novo = parse_valor(novo_valor_original)
    p = _travar(parcela)
    _exigir_editavel(p)
    if novo <= 0:
        raise ValidationError("O valor deve ser maior que zero.")
    if novo < p.desconto:
        raise ValidationError("O valor não pode ser menor que o desconto concedido.")
    anterior, devido_antes = p.valor_original, p.valor_devido
    p.valor_original = novo
    _persistir_valores(p)
    _registrar_ajuste(p, usuario, "VALOR", anterior, novo, devido_antes, justificativa)
    return p


@transaction.atomic
def cancelar_parcela(parcela, usuario, justificativa):
    justificativa = _exigir_justificativa(justificativa)
    p = _travar(parcela)
    if p.status == Parcela.Status.CANCELADA:
        raise ValidationError("A parcela já está cancelada.")
    if p.valor_pago > 0 or p.alocacoes.filter(estornada=False).exists():
        raise ValidationError("Parcela com pagamento não pode ser cancelada. Estorne o recebimento primeiro.")
    anterior, devido_antes = p.get_status_display(), p.valor_devido
    p.status = Parcela.Status.CANCELADA
    p.save(update_fields=["status"])
    _registrar_ajuste(p, usuario, "CANCELAMENTO", anterior, "Cancelado", devido_antes, justificativa)
    return p


@transaction.atomic
def cancelar_parcelas_da_matricula(matricula, usuario, justificativa):
    n = 0
    for p in Parcela.objects.filter(matricula=matricula, status=Parcela.Status.ABERTA, valor_pago=0):
        cancelar_parcela(p, usuario, justificativa)
        n += 1
    return n


# --------------------------------------------------------------------------- encargos

def calcular_encargos(parcela, hoje=None):
    """Multa + juros simples por atraso sobre o principal (original − desconto). Zero se não aplicável."""
    hoje = hoje or timezone.localdate()
    c = parcela.contrato
    if not c.aplicar_encargos or parcela.status not in (Parcela.Status.ABERTA, Parcela.Status.PARCIAL):
        return ZERO
    if parcela.vencimento >= hoje:
        return ZERO
    principal = parcela.valor_original - parcela.desconto
    dias = (hoje - parcela.vencimento).days
    multa = principal * c.multa_percentual / Decimal(100)
    juros = principal * c.juros_mensal_percentual / Decimal(100) * Decimal(dias) / Decimal(30)
    return q2(multa + juros)


def acrescimo_atual(parcela, hoje=None):
    """Acréscimo vigente = maior entre o já gravado e o calculado (nunca diminui sozinho)."""
    return max(parcela.acrescimo, calcular_encargos(parcela, hoje))


def _atualizar_encargos(p, usuario, hoje):
    novo = acrescimo_atual(p, hoje)
    if novo != p.acrescimo:
        anterior, devido_antes = p.acrescimo, p.valor_devido
        p.acrescimo = novo
        p.recalcular_valor_devido()
        p.save(update_fields=["acrescimo", "valor_devido"])
        _registrar_ajuste(p, usuario, "ENCARGOS", anterior, novo, devido_antes, "Multa/juros por atraso")


# --------------------------------------------------------------------------- recebimento

@transaction.atomic
def registrar_recebimento(*, aluno, itens, forma, valor_recebido, usuario, token, referencia="", hoje=None):
    """Registra um recebimento de forma atômica e idempotente.

    itens: lista de (parcela_id, valor_a_receber). Retorna (recebimento, criado).
    Reenviar o mesmo `token` devolve o recebimento já criado, sem duplicar nada.
    """
    hoje = hoje or timezone.localdate()
    inst = aluno.instituicao

    existente = Recebimento.objects.filter(token_idempotencia=token).first()
    if existente:
        return existente, False

    if forma not in Recebimento.Forma.values:
        raise ValidationError("Forma de pagamento inválida.")
    if not itens:
        raise ValidationError("Selecione ao menos uma parcela.")
    ids = [pid for pid, _ in itens]
    if len(set(ids)) != len(ids):
        raise ValidationError("Parcela selecionada mais de uma vez.")
    valores = {pid: parse_valor(v) for pid, v in itens}

    parcelas = list(Parcela.objects.select_for_update().select_related("instituicao", "contrato", "responsavel")
                    .filter(pk__in=ids, aluno=aluno, instituicao=inst).order_by("pk"))
    if len(parcelas) != len(ids):
        raise ValidationError("Há parcelas que não pertencem a este aluno.")

    # Reconsulta o token já com as linhas travadas (requisição concorrente idêntica).
    existente = Recebimento.objects.filter(token_idempotencia=token).first()
    if existente:
        return existente, False

    total = ZERO
    for p in parcelas:
        if p.status not in (Parcela.Status.ABERTA, Parcela.Status.PARCIAL):
            raise ValidationError(f"A parcela {p.numero} ({p.descricao}) não está em aberto.")
        _atualizar_encargos(p, usuario, hoje)
        v = valores[p.pk]
        if v <= 0:
            raise ValidationError("O valor de cada parcela deve ser maior que zero.")
        if v > p.saldo:
            raise ValidationError(f"O valor da parcela {p.numero} excede o saldo (R$ {p.saldo}).")
        total += v

    if forma == Recebimento.Forma.DINHEIRO:
        recebido = parse_valor(valor_recebido)
        if recebido < total:
            raise ValidationError("O valor recebido é menor que o total a receber.")
        troco = recebido - total
    else:
        recebido, troco = total, ZERO

    responsaveis = {p.responsavel_id for p in parcelas}
    try:
        with transaction.atomic():
            rec = Recebimento(
                instituicao=inst, numero=gerar_numero_recebimento(inst, hoje.year), aluno=aluno,
                responsavel=parcelas[0].responsavel if len(responsaveis) == 1 else None,
                forma=forma, valor_total=total, valor_recebido=recebido, troco=troco,
                referencia=(referencia or "").strip(), token_idempotencia=token, operador=usuario,
                operador_nome=usuario.get_username())
            rec.full_clean(exclude=["responsavel", "operador"], validate_unique=False, validate_constraints=False)
            rec.save()
    except IntegrityError:
        existente = Recebimento.objects.filter(token_idempotencia=token).first()
        if existente:
            return existente, False
        raise

    for p in parcelas:
        v = valores[p.pk]
        AlocacaoRecebimento.objects.create(recebimento=rec, parcela=p, valor=v)
        p.valor_pago += v
        recalcular_status(p)
        p.quitacao = hoje if p.status == Parcela.Status.QUITADA else None
        p.save(update_fields=["valor_pago", "status", "quitacao"])

    registrar(instituicao=inst, usuario=usuario, acao="RECEBIMENTO_REGISTRADO", entidade="Recebimento",
              entidade_id=rec.pk, aluno_id=aluno.pk, descricao=f"Recebimento {rec.numero}",
              dados={"numero": rec.numero, "forma": forma, "valor_total": total, "troco": troco,
                     "parcelas": ids})
    return rec, True


@transaction.atomic
def estornar_recebimento(recebimento, usuario, justificativa):
    """Estorno administrativo: preserva o recebimento, reverte alocações e recalcula saldos."""
    justificativa = _exigir_justificativa(justificativa)
    rec = Recebimento.objects.select_for_update().select_related("instituicao", "aluno").get(pk=recebimento.pk)
    if rec.status == Recebimento.Status.ESTORNADO:
        raise ValidationError("Este recebimento já foi estornado.")

    aloc = list(rec.alocacoes.select_for_update().select_related("parcela").order_by("parcela_id"))
    parcelas = {a.parcela_id: a.parcela for a in aloc}
    Parcela.objects.select_for_update().filter(pk__in=parcelas).order_by("pk").count()  # trava as linhas

    rec.alocacoes.update(estornada=True)
    for pid in parcelas:
        p = Parcela.objects.get(pk=pid)
        p.valor_pago = (AlocacaoRecebimento.objects.filter(parcela=p, estornada=False)
                        .aggregate(s=Sum("valor"))["s"] or ZERO)
        if p.status != Parcela.Status.CANCELADA:
            p.status = Parcela.Status.ABERTA
            recalcular_status(p)
        p.save(update_fields=["valor_pago", "status", "quitacao"])

    rec.status = Recebimento.Status.ESTORNADO
    rec.save(update_fields=["status"])
    Estorno.objects.create(recebimento=rec, justificativa=justificativa, usuario=usuario,
                           usuario_nome=usuario.get_username())
    registrar(instituicao=rec.instituicao, usuario=usuario, acao="RECEBIMENTO_ESTORNADO", entidade="Recebimento",
              entidade_id=rec.pk, aluno_id=rec.aluno_id, descricao=justificativa,
              dados={"numero": rec.numero, "valor_total": rec.valor_total, "parcelas": list(parcelas)})
    return rec
