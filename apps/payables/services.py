"""Regras do contas a pagar. Toda operação que altera dinheiro roda em transação, com bloqueio de linha.

Saldo disponível = recebimentos CONFIRMADOS (valor aplicado às parcelas, sem troco) − liquidações não estornadas.
O saldo é sempre calculado (nunca armazenado), então estornos de recebimentos e de liquidações o refletem sozinhos.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import registrar
from apps.core.models import ConfiguracaoGeral
from apps.core.numbering import proxima_sequencia
from apps.finance.models import Recebimento
from apps.finance.services import parse_valor, somar_meses

from .models import ContaPagar, Liquidacao

ZERO = Decimal("0.00")
TIPO_SEQUENCIA = "CONTA_PAGAR"
MAX_REPETICOES = 36


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ----------------------------------------------------------------------------- saldo e configuração
def configuracao(instituicao):
    cfg, _ = ConfiguracaoGeral.objects.get_or_create(instituicao=instituicao)
    return cfg


def saldo(instituicao):
    """Dicionário com total recebido, total pago e saldo disponível."""
    recebido = (Recebimento.objects.filter(instituicao=instituicao, status="CONFIRMADO")
                .aggregate(s=Sum("valor_total"))["s"] or ZERO)
    pago = (Liquidacao.objects.filter(instituicao=instituicao, estornada=False)
            .aggregate(s=Sum("valor"))["s"] or ZERO)
    return {"recebido": recebido, "pago": pago, "saldo": recebido - pago}


def resumo(instituicao, hoje=None):
    """Indicadores da tela de contas a pagar (da escola inteira, independentes de filtros)."""
    hoje = hoje or timezone.localdate()
    abertas = ContaPagar.objects.filter(instituicao=instituicao, status=ContaPagar.Status.ABERTA)
    soma = lambda qs: qs.aggregate(s=Sum("valor"))["s"] or ZERO  # noqa: E731
    dias = configuracao(instituicao).dias_aviso_contas_pagar
    pago_mes = (Liquidacao.objects.filter(instituicao=instituicao, estornada=False,
                                          data__year=hoje.year, data__month=hoje.month)
                .aggregate(s=Sum("valor"))["s"] or ZERO)
    return {**saldo(instituicao), "em_aberto": soma(abertas),
            "vencido": soma(abertas.filter(vencimento__lt=hoje)),
            "qtd_vencidas": abertas.filter(vencimento__lt=hoje).count(),
            "a_vencer": soma(abertas.filter(vencimento__gte=hoje, vencimento__lte=hoje + timedelta(days=dias))),
            "dias_aviso": dias, "pago_no_mes": pago_mes}


# ----------------------------------------------------------------------------- notificações
def contas_a_vencer(instituicao, hoje=None, dias=None):
    """Contas em aberto vencidas ou que vencem dentro do prazo de aviso, da mais urgente para a menos."""
    hoje = hoje or timezone.localdate()
    if dias is None:
        dias = configuracao(instituicao).dias_aviso_contas_pagar
    return (ContaPagar.objects.filter(instituicao=instituicao, status=ContaPagar.Status.ABERTA,
                                      vencimento__lte=hoje + timedelta(days=dias)).order_by("vencimento", "id"))


# ----------------------------------------------------------------------------- cadastro
def _numero(instituicao, ano):
    return f"CP-{ano}-{proxima_sequencia(instituicao, TIPO_SEQUENCIA, f'ANO:{ano}'):05d}"


def _campos_conta(dados):
    return {k: dados[k] for k in ("descricao", "categoria", "prestador", "prestador_documento",
                                  "numero_documento", "valor", "vencimento", "observacoes") if k in dados}


def _ja_criadas(instituicao, token):
    """Contas já criadas por este envio do formulário (None se ainda não existem)."""
    base = ContaPagar.objects.filter(instituicao=instituicao, token_criacao=token).first()
    if base is None:
        return None
    if base.grupo:
        return list(ContaPagar.objects.filter(instituicao=instituicao, grupo=base.grupo).order_by("parcela_numero"))
    return [base]


@transaction.atomic
def criar_contas(*, instituicao, usuario, dados, repeticoes=1, token=None):
    """Cria 1 conta ou uma por mês (`repeticoes`), com o mesmo valor. Devolve a lista de contas.

    `token` torna a operação idempotente: reenviar o mesmo formulário devolve as contas já criadas.
    """
    repeticoes = repeticoes or 1
    if not 1 <= repeticoes <= MAX_REPETICOES:
        raise ValidationError(f"A repetição deve ser de 1 a {MAX_REPETICOES} meses.")
    if token:
        existente = _ja_criadas(instituicao, token)
        if existente:
            return existente
    campos = _campos_conta(dados)
    valor = parse_valor(campos["valor"])
    if valor <= 0:
        raise ValidationError("O valor da conta deve ser maior que zero.")
    primeira = campos["vencimento"]
    grupo = uuid4() if repeticoes > 1 else None
    hoje = timezone.localdate()
    contas = []
    try:
        with transaction.atomic():
            for i in range(repeticoes):
                venc = somar_meses(primeira, i, primeira.day)
                descricao = campos["descricao"] if repeticoes == 1 else f"{campos['descricao']} ({i + 1}/{repeticoes})"
                conta = ContaPagar(
                    instituicao=instituicao, numero=_numero(instituicao, hoje.year),
                    **{**campos, "descricao": descricao[:150], "valor": valor, "vencimento": venc},
                    grupo=grupo, parcela_numero=i + 1, parcela_total=repeticoes,
                    token_criacao=token if i == 0 else None,
                    criado_por=usuario if getattr(usuario, "pk", None) else None)
                conta.save()
                contas.append(conta)
    except IntegrityError:
        existente = _ja_criadas(instituicao, token) if token else None
        if existente:  # envio duplicado simultâneo
            return existente
        raise
    registrar(instituicao=instituicao, usuario=usuario, acao="CONTA_PAGAR_CRIADA", entidade="ContaPagar",
              entidade_id=contas[0].pk, descricao=f"Conta a pagar {contas[0].numero}",
              dados={"numero": contas[0].numero, "valor": valor, "vencimento": primeira, "categoria": campos["categoria"],
                     "quantidade": repeticoes})
    return contas


def _travar(conta):
    return ContaPagar.objects.select_for_update().select_related("instituicao").get(pk=conta.pk)


@transaction.atomic
def editar_conta(conta, dados, usuario):
    """Só contas em aberto podem ser editadas (liquidada/desativada preservam o histórico)."""
    c = _travar(conta)
    if c.status != ContaPagar.Status.ABERTA:
        raise ValidationError("Somente contas em aberto podem ser editadas.")
    novos = _campos_conta(dados)
    novos["valor"] = parse_valor(novos["valor"])
    if novos["valor"] <= 0:
        raise ValidationError("O valor da conta deve ser maior que zero.")
    alterados = [k for k, v in novos.items() if getattr(c, k) != v]
    for k, v in novos.items():
        setattr(c, k, v)
    c.save()
    registrar(instituicao=c.instituicao, usuario=usuario, acao="CONTA_PAGAR_ALTERADA", entidade="ContaPagar",
              entidade_id=c.pk, descricao=f"Conta a pagar {c.numero} alterada", dados={"campos": alterados})
    return c


# ----------------------------------------------------------------------------- liquidação e situação
def _justificativa(texto):
    texto = (texto or "").strip()
    if len(texto) < 5:
        raise ValidationError("Informe a justificativa (mínimo de 5 caracteres).")
    return texto[:255]


@transaction.atomic
def liquidar_conta(conta, *, usuario, data, forma, valor=None, referencia="", observacao=""):
    """Liquida a conta e debita o saldo.

    Serializa por escola (trava a linha de configurações): duas liquidações simultâneas não conseguem gastar o
    mesmo saldo. Sem permissão de saldo negativo, recusa a liquidação que deixaria o saldo abaixo de zero.
    """
    cfg = ConfiguracaoGeral.objects.select_for_update().get_or_create(instituicao=conta.instituicao)[0]
    c = _travar(conta)
    if c.status == ContaPagar.Status.LIQUIDADA:
        raise ValidationError("Esta conta já foi liquidada.")
    if c.status == ContaPagar.Status.DESATIVADA:
        raise ValidationError("Conta desativada não pode ser liquidada. Reative-a primeiro.")
    valor = c.valor if valor in (None, "") else parse_valor(valor)
    if valor <= 0:
        raise ValidationError("O valor liquidado deve ser maior que zero.")
    if data > timezone.localdate():
        raise ValidationError("A data da liquidação não pode ser futura.")
    s = saldo(c.instituicao)
    if not cfg.permitir_saldo_negativo and s["saldo"] - valor < 0:
        raise ValidationError(
            f"Não é permitido saldo negativo. Saldo disponível: {brl(s['saldo'])}; "
            f"valor a liquidar: {brl(valor)}.")
    liq = Liquidacao(conta=c, valor=valor, data=data, forma=forma, referencia=referencia, observacao=observacao,
                     usuario=usuario if getattr(usuario, "pk", None) else None,
                     usuario_nome=usuario.get_username() if usuario else "")
    liq.save()
    c.status = ContaPagar.Status.LIQUIDADA
    c.save(update_fields=["status"])
    registrar(instituicao=c.instituicao, usuario=usuario, acao="CONTA_PAGAR_LIQUIDADA", entidade="ContaPagar",
              entidade_id=c.pk, descricao=f"Conta {c.numero} liquidada",
              dados={"numero": c.numero, "valor": valor, "forma": forma, "saldo_apos": s["saldo"] - valor,
                     "saldo_negativo": s["saldo"] - valor < 0})
    return liq


@transaction.atomic
def desativar_conta(conta, usuario, motivo):
    c = _travar(conta)
    if c.status == ContaPagar.Status.LIQUIDADA:
        raise ValidationError("Conta liquidada não pode ser desativada. Estorne a liquidação antes.")
    if c.status == ContaPagar.Status.DESATIVADA:
        raise ValidationError("Esta conta já está desativada.")
    c.status = ContaPagar.Status.DESATIVADA
    c.motivo_desativacao = _justificativa(motivo)
    c.desativada_em = timezone.now()
    c.desativada_por = usuario if getattr(usuario, "pk", None) else None
    c.save(update_fields=["status", "motivo_desativacao", "desativada_em", "desativada_por"])
    registrar(instituicao=c.instituicao, usuario=usuario, acao="CONTA_PAGAR_DESATIVADA", entidade="ContaPagar",
              entidade_id=c.pk, descricao=f"Conta {c.numero} desativada", dados={"motivo": c.motivo_desativacao})
    return c


@transaction.atomic
def reativar_conta(conta, usuario):
    c = _travar(conta)
    if c.status != ContaPagar.Status.DESATIVADA:
        raise ValidationError("Somente contas desativadas podem ser reativadas.")
    motivo = c.motivo_desativacao
    c.status = ContaPagar.Status.ABERTA
    c.motivo_desativacao, c.desativada_em, c.desativada_por = "", None, None
    c.save(update_fields=["status", "motivo_desativacao", "desativada_em", "desativada_por"])
    registrar(instituicao=c.instituicao, usuario=usuario, acao="CONTA_PAGAR_REATIVADA", entidade="ContaPagar",
              entidade_id=c.pk, descricao=f"Conta {c.numero} reativada", dados={"motivo_anterior": motivo})
    return c


@transaction.atomic
def estornar_liquidacao(conta, usuario, justificativa):
    """Desfaz a liquidação (erro de lançamento): preserva o registro, devolve o valor ao saldo e reabre a conta."""
    justificativa = _justificativa(justificativa)
    ConfiguracaoGeral.objects.select_for_update().get_or_create(instituicao=conta.instituicao)
    c = _travar(conta)
    if c.status != ContaPagar.Status.LIQUIDADA:
        raise ValidationError("Somente contas liquidadas podem ter a liquidação estornada.")
    liq = Liquidacao.objects.select_for_update().get(conta=c, estornada=False)
    liq.estornada = True
    liq.estorno_justificativa = justificativa
    liq.estorno_usuario = usuario if getattr(usuario, "pk", None) else None
    liq.estorno_usuario_nome = usuario.get_username() if usuario else ""
    liq.estornada_em = timezone.now()
    liq.save()
    c.status = ContaPagar.Status.ABERTA
    c.save(update_fields=["status"])
    registrar(instituicao=c.instituicao, usuario=usuario, acao="CONTA_PAGAR_LIQUIDACAO_ESTORNADA",
              entidade="ContaPagar", entidade_id=c.pk, descricao=f"Liquidação da conta {c.numero} estornada",
              dados={"valor": liq.valor, "justificativa": justificativa})
    return c
