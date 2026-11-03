"""Regras da cobrança bancária: configuração, boleto de teste, ativação, registro de boletos, baixa e sincronização.

Fluxo da escola:
    1. `salvar_configuracao`  → informa provedor, ambiente e chave de API;
    2. `gerar_boleto_teste`   → emite um boleto de teste (não é cobrança de aluno e não gera recebimento);
    3. `consultar_e_baixar`   → confirma que o pagamento do teste chegou (por consulta ou aviso do banco);
    4. `ativar_cobranca`      → só agora as parcelas passam a ser registradas no banco.

Depois de ativa, toda parcela nova (e as antigas, por `gerar_em_lote`) ganha boleto registrado com QR Code Pix.
O aviso de pagamento do banco vira um `Recebimento` (forma BOLETO) pelo mesmo serviço do caixa, com o token da
cobrança como chave de idempotência.
"""
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import registrar
from apps.finance import services as fin
from apps.finance.models import Parcela, Recebimento
from apps.students.utils import cnpj_valido, cpf_valido

from .models import Cobranca, ConfiguracaoBancaria, EventoBanco
from .providers import ErroProvedor, provedor_da
from .providers.base import Pagador, Titulo

log = logging.getLogger(__name__)
ZERO = Decimal("0.00")
VALOR_TESTE_PADRAO = Decimal("5.00")
LIMITE_LOTE = 100


# --------------------------------------------------------------------------- auditoria
def _audit(instituicao, usuario, acao, descricao, entidade="Cobranca", entidade_id="", aluno_id=None, **dados):
    registrar(instituicao=instituicao, usuario=usuario, acao=acao, entidade=entidade, entidade_id=entidade_id,
              aluno_id=aluno_id, descricao=descricao, dados=dados)


# --------------------------------------------------------------------------- configuração
def configuracao(instituicao):
    cfg, criada = ConfiguracaoBancaria.objects.get_or_create(instituicao=instituicao)
    if not cfg.webhook_token_cifrado:
        cfg.definir_webhook_token(secrets.token_urlsafe(32))
        cfg.save(update_fields=["webhook_token_cifrado"])
    return cfg


def salvar_configuracao(instituicao, dados, usuario):
    """Grava os dados do banco. A chave de API só é trocada quando um valor novo é informado."""
    cfg = configuracao(instituicao)
    antes = cfg.assinatura()
    for campo in ("provedor", "ambiente", "banco_nome", "agencia", "conta", "beneficiario_nome",
                  "beneficiario_documento", "instrucoes"):
        if campo in dados:
            setattr(cfg, campo, dados[campo])
    chave_nova = bool((dados.get("api_key") or "").strip())
    if chave_nova:
        cfg.definir_api_key(dados["api_key"])
    reaberta = False
    if cfg.status == ConfiguracaoBancaria.Status.ATIVA and cfg.assinatura() != antes:
        cfg.status = ConfiguracaoBancaria.Status.CONFIGURANDO  # trocou banco/ambiente/chave: precisa testar de novo
        reaberta = True
    cfg.save()
    _audit(instituicao, usuario, "COBRANCA_CONFIG_ALTERADA", "Configuração da cobrança bancária alterada",
           entidade="ConfiguracaoBancaria", entidade_id=cfg.pk, provedor=cfg.provedor, ambiente=cfg.ambiente,
           chave_alterada=chave_nova, reaberta_para_teste=reaberta)
    return cfg, reaberta


def novo_token_webhook(instituicao, usuario):
    cfg = configuracao(instituicao)
    cfg.definir_webhook_token(secrets.token_urlsafe(32))
    cfg.save(update_fields=["webhook_token_cifrado"])
    _audit(instituicao, usuario, "COBRANCA_WEBHOOK_TOKEN_RENOVADO", "Token do webhook renovado",
           entidade="ConfiguracaoBancaria", entidade_id=cfg.pk)
    return cfg


def testar_conexao(cfg):
    """Confere credenciais/ambiente no provedor. Levanta ValidationError com a mensagem do banco."""
    if not cfg.completa:
        raise ValidationError("Informe a chave de API do provedor antes de testar a conexão.")
    try:
        provedor_da(cfg).verificar()
    except ErroProvedor as exc:
        raise ValidationError(str(exc)) from None


def teste_confirmado(cfg):
    """Há um boleto de teste PAGO emitido com a configuração (provedor/ambiente/chave) que está valendo?"""
    return Cobranca.objects.filter(instituicao=cfg.instituicao, eh_teste=True, status=Cobranca.Status.PAGA,
                                   assinatura=cfg.assinatura()).exists()


def etapas(cfg):
    """Estado das 4 etapas da tela de configuração."""
    ult = (Cobranca.objects.filter(instituicao=cfg.instituicao, eh_teste=True, assinatura=cfg.assinatura())
           .exclude(status=Cobranca.Status.CANCELADA).order_by("-criado_em", "-id").first())
    confirmado = teste_confirmado(cfg)
    return {"configurada": cfg.completa, "teste": ult, "teste_gerado": ult is not None, "confirmado": confirmado,
            "ativa": cfg.status == ConfiguracaoBancaria.Status.ATIVA}


def ativar_cobranca(cfg, usuario):
    if not teste_confirmado(cfg):
        raise ValidationError("Gere o boleto de teste e confirme o recebimento antes de ativar a cobrança.")
    cfg.status = ConfiguracaoBancaria.Status.ATIVA
    cfg.ativada_em, cfg.ativada_por = timezone.now(), usuario if getattr(usuario, "pk", None) else None
    cfg.save(update_fields=["status", "ativada_em", "ativada_por"])
    _audit(cfg.instituicao, usuario, "COBRANCA_ATIVADA", "Cobrança com registro bancário ativada",
           entidade="ConfiguracaoBancaria", entidade_id=cfg.pk, provedor=cfg.provedor, ambiente=cfg.ambiente)
    return cfg


def suspender_cobranca(cfg, usuario):
    cfg.status = ConfiguracaoBancaria.Status.SUSPENSA
    cfg.save(update_fields=["status"])
    _audit(cfg.instituicao, usuario, "COBRANCA_SUSPENSA", "Cobrança com registro bancário suspensa",
           entidade="ConfiguracaoBancaria", entidade_id=cfg.pk)
    return cfg


def cobranca_ativa(instituicao):
    """Configuração, se (e somente se) a escola está habilitada a registrar boletos; senão None."""
    cfg = ConfiguracaoBancaria.objects.filter(instituicao=instituicao,
                                              status=ConfiguracaoBancaria.Status.ATIVA).first()
    return cfg if cfg is not None and teste_confirmado(cfg) else None


# --------------------------------------------------------------------------- boleto de teste
def _documento_valido(texto):
    d = re.sub(r"\D", "", texto or "")
    return d if (len(d) == 11 and cpf_valido(d)) or (len(d) == 14 and cnpj_valido(d)) else ""


@transaction.atomic
def gerar_boleto_teste(cfg, usuario, *, pagador_nome, pagador_documento, pagador_email="", valor=VALOR_TESTE_PADRAO,
                       hoje=None):
    """Emite um boleto de teste (R$ 5,00 por padrão). Não é cobrança de aluno e **não** gera recebimento."""
    if not cfg.completa:
        raise ValidationError("Salve os dados do banco (inclusive a chave de API) antes de gerar o teste.")
    documento = _documento_valido(pagador_documento)
    if not documento:
        raise ValidationError("Informe um CPF ou CNPJ válido para o pagador do teste.")
    hoje = hoje or timezone.localdate()
    for antigo in Cobranca.objects.filter(instituicao=cfg.instituicao, eh_teste=True,
                                          status=Cobranca.Status.PENDENTE):  # um teste pendente por vez
        _cancelar_no_banco(cfg, antigo, silencioso=True)
        antigo.status = Cobranca.Status.CANCELADA
        antigo.save(update_fields=["status"])
    titulo = Titulo(referencia=f"teste-{secrets.token_hex(4)}", valor=Decimal(valor), vencimento=hoje + timedelta(days=5),
                    descricao="Boleto de teste da configuração de cobrança (EduNex)",
                    pagador=Pagador(nome=pagador_nome.strip(), documento=documento, email=pagador_email.strip()))
    try:
        emitido = provedor_da(cfg).emitir(titulo)
    except ErroProvedor as exc:
        raise ValidationError(str(exc)) from None
    cob = _gravar(Cobranca(instituicao=cfg.instituicao, eh_teste=True, criado_por=_user(usuario)), cfg, titulo, emitido)
    _audit(cfg.instituicao, usuario, "COBRANCA_TESTE_GERADA", "Boleto de teste gerado", entidade_id=cob.pk,
           valor=cob.valor, provedor=cfg.provedor, ambiente=cfg.ambiente)
    return cob


def _user(usuario):
    return usuario if getattr(usuario, "pk", None) else None


def _gravar(cob, cfg, titulo, emitido):
    cob.assinatura, cob.provedor, cob.ambiente = cfg.assinatura(), cfg.provedor, cfg.ambiente
    cob.id_externo, cob.nosso_numero = emitido.id_externo, emitido.nosso_numero
    cob.linha_digitavel, cob.codigo_barras = emitido.linha_digitavel, emitido.codigo_barras
    cob.pix_copia_cola, cob.pix_qrcode = emitido.pix_copia_cola, emitido.pix_qrcode
    cob.url_boleto, cob.url_fatura = emitido.url_boleto, emitido.url_fatura
    cob.descricao, cob.pagador_nome = titulo.descricao[:200], titulo.pagador.nome[:150]
    cob.valor, cob.vencimento = titulo.valor, titulo.vencimento
    cob.status, cob.erro = Cobranca.Status.PENDENTE, ""
    cob.save()
    return cob


# --------------------------------------------------------------------------- boleto de parcela
def _valores(p, hoje):
    """(valor, vencimento, multa %, juros %) que o boleto da parcela deve ter hoje.

    Parcela a vencer: valor do principal e multa/juros do contrato (o banco calcula o atraso).
    Parcela já vencida: o banco não aceita vencimento no passado; emite para hoje com os encargos já somados.
    """
    c = p.contrato
    principal = p.valor_original - p.desconto - p.valor_pago
    if p.vencimento >= hoje:
        multa = c.multa_percentual if c.aplicar_encargos else ZERO
        juros = c.juros_mensal_percentual if c.aplicar_encargos else ZERO
        return principal, p.vencimento, multa, juros
    return principal + fin.acrescimo_atual(p, hoje), hoje, ZERO, ZERO


def _titulo_da_parcela(p, hoje):
    r = p.responsavel
    documento = _documento_valido(r.documento)
    if not documento:
        raise ValidationError(f"O responsável financeiro {r.nome} não tem CPF/CNPJ válido. Corrija o cadastro do "
                              "responsável e gere o boleto novamente.")
    valor, venc, multa, juros = _valores(p, hoje)
    if valor <= 0:
        raise ValidationError("A parcela não tem saldo a cobrar.")
    atrasada = p.vencimento < hoje
    descricao = p.descricao + (f" (venc. original {p.vencimento:%d/%m/%Y})" if atrasada else "")
    pagador = Pagador(nome=r.nome, documento=documento, email=r.email_cobranca, telefone=r.telefone, cep=r.cep,
                      numero_endereco=r.numero)
    return Titulo(referencia=f"parcela-{p.pk}", valor=valor, vencimento=venc, descricao=descricao, pagador=pagador,
                  multa_percentual=multa, juros_mensal_percentual=juros)


def registrar_cobranca(parcela, usuario=None, hoje=None):
    """Registra o boleto (com Pix) da parcela no banco. Idempotente: parcela com boleto vigente devolve o existente.

    Falha do banco ou dado faltando NÃO levanta erro: grava a cobrança como ERRO (com a mensagem) para o financeiro
    corrigir e tentar de novo. Levanta ValidationError só quando a cobrança não está ativa ou a parcela não é cobrável.
    """
    hoje = hoje or timezone.localdate()
    with transaction.atomic():
        p = (Parcela.objects.select_for_update(of=("self",)).select_related("instituicao", "responsavel", "contrato", "aluno")
             .get(pk=parcela.pk))
        vigente = Cobranca.objects.filter(parcela=p, status__in=[Cobranca.Status.PENDENTE, Cobranca.Status.PAGA]).first()
        if vigente:
            return vigente
        cfg = cobranca_ativa(p.instituicao)
        if cfg is None:
            raise ValidationError("A cobrança com registro bancário não está ativa nesta escola.")
        if p.status not in (Parcela.Status.ABERTA, Parcela.Status.PARCIAL):
            raise ValidationError("Só parcelas em aberto podem ter boleto.")
        cob = (Cobranca.objects.filter(parcela=p, status=Cobranca.Status.ERRO).first()
               or Cobranca(instituicao=p.instituicao, parcela=p, criado_por=_user(usuario)))
        try:
            titulo = _titulo_da_parcela(p, hoje)
            emitido = provedor_da(cfg).emitir(titulo)
        except (ValidationError, ErroProvedor) as exc:
            cob.assinatura, cob.provedor, cob.ambiente = cfg.assinatura(), cfg.provedor, cfg.ambiente
            cob.valor = max(p.valor_devido - p.valor_pago, Decimal("0.01"))
            cob.vencimento, cob.pagador_nome = max(p.vencimento, hoje), p.responsavel.nome[:150]
            cob.status = Cobranca.Status.ERRO
            cob.erro = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            cob.save()
            _audit(p.instituicao, usuario, "COBRANCA_ERRO", f"Boleto não registrado: {cob.erro[:150]}",
                   entidade_id=cob.pk, aluno_id=p.aluno_id, parcela=p.pk)
            return cob
        _gravar(cob, cfg, titulo, emitido)
        _audit(p.instituicao, usuario, "COBRANCA_REGISTRADA", f"Boleto registrado — {p.descricao}",
               entidade_id=cob.pk, aluno_id=p.aluno_id, parcela=p.pk, valor=cob.valor,
               vencimento=cob.vencimento.isoformat())
        return cob


def pendentes_sem_boleto(instituicao):
    return (Parcela.objects.filter(instituicao=instituicao, status__in=[Parcela.Status.ABERTA, Parcela.Status.PARCIAL])
            .exclude(cobrancas__status__in=[Cobranca.Status.PENDENTE, Cobranca.Status.PAGA]).distinct())


def gerar_em_lote(instituicao, usuario, limite=LIMITE_LOTE, hoje=None, incluir_erros=False):
    """Registra boletos das parcelas em aberto que ainda não têm (até `limite` por vez).

    Parcelas cuja tentativa anterior deu erro só entram com `incluir_erros=True` (evita repetir sempre as mesmas).
    """
    if cobranca_ativa(instituicao) is None:
        raise ValidationError("A cobrança com registro bancário não está ativa nesta escola.")
    base = pendentes_sem_boleto(instituicao)
    if not incluir_erros:
        base = base.exclude(cobrancas__status=Cobranca.Status.ERRO)
    ok = erro = 0
    for p in list(base.order_by("vencimento", "id")[:limite]):
        if registrar_cobranca(p, usuario, hoje=hoje).status == Cobranca.Status.ERRO:
            erro += 1
        else:
            ok += 1
    _audit(instituicao, usuario, "COBRANCA_LOTE", f"Boletos em lote: {ok} registrados, {erro} com erro",
           registrados=ok, erros=erro)
    return {"ok": ok, "erro": erro, "restantes": base.count()}


# --------------------------------------------------------------------------- cancelar / sincronizar
def _cancelar_no_banco(cfg, cob, silencioso=False):
    if not cob.id_externo:
        return
    try:
        provedor_da(cfg).cancelar(cob.id_externo)
    except ErroProvedor as exc:
        if not silencioso:
            raise ValidationError(f"O banco não cancelou o boleto: {exc}") from None
        log.warning("Cancelamento de boleto de teste falhou: %s", exc)


def cancelar_cobranca(cob, usuario=None, motivo=""):
    """Cancela o boleto no banco e na escola. Se o banco informar que já foi pago, o pagamento é baixado."""
    c = Cobranca.objects.select_related("instituicao", "parcela").get(pk=cob.pk)
    if c.status != Cobranca.Status.PENDENTE:
        raise ValidationError("Só boletos aguardando pagamento podem ser cancelados.")
    cfg = configuracao(c.instituicao)
    if c.id_externo:
        try:
            sit = provedor_da(cfg).consultar(c.id_externo)
        except ErroProvedor as exc:
            raise ValidationError(str(exc)) from None
        if sit.status == "PAGA":
            baixar_cobranca(c, valor_pago=sit.valor_pago, data=sit.data_pagamento, usuario=usuario)
            raise ValidationError("Este boleto já foi pago no banco; o pagamento foi registrado e ele não pode ser cancelado.")
    with transaction.atomic():
        c = Cobranca.objects.select_for_update(of=("self",)).select_related("instituicao", "parcela").get(pk=cob.pk)
        if c.status != Cobranca.Status.PENDENTE:
            raise ValidationError("Só boletos aguardando pagamento podem ser cancelados.")
        _cancelar_no_banco(cfg, c)
        c.status = Cobranca.Status.CANCELADA
        c.save(update_fields=["status", "atualizado_em"])
        _audit(c.instituicao, usuario, "COBRANCA_CANCELADA", f"Boleto cancelado. {motivo}".strip(), entidade_id=c.pk,
               aluno_id=c.parcela.aluno_id if c.parcela_id else None)
        return c


def sincronizar_parcela(parcela, usuario=None, hoje=None):
    """Mantém o boleto pendente igual à parcela: cancela se a parcela foi cancelada/quitada; altera valor/vencimento."""
    hoje = hoje or timezone.localdate()
    with transaction.atomic():
        c = (Cobranca.objects.select_for_update(of=("self",)).filter(parcela=parcela, status=Cobranca.Status.PENDENTE)
             .select_related("instituicao").first())
        if c is None:
            return None
        p = Parcela.objects.select_related("contrato", "instituicao").get(pk=parcela.pk)
        cfg = configuracao(c.instituicao)
        if p.status in (Parcela.Status.CANCELADA, Parcela.Status.QUITADA):
            motivo = "parcela cancelada" if p.status == Parcela.Status.CANCELADA else "parcela quitada por outro meio"
            _cancelar_no_banco(cfg, c)
            c.status = Cobranca.Status.CANCELADA
            c.save(update_fields=["status", "atualizado_em"])
            _audit(c.instituicao, usuario, "COBRANCA_CANCELADA", f"Boleto cancelado: {motivo}", entidade_id=c.pk,
                   aluno_id=p.aluno_id)
            return c
        valor, venc, _m, _j = _valores(p, hoje)
        if valor == c.valor and venc == c.vencimento:
            return c
        try:
            novo = provedor_da(cfg).alterar(c.id_externo, valor=valor, vencimento=venc)
        except ErroProvedor as exc:
            c.divergencia = f"Parcela alterada, mas o banco não atualizou o boleto: {exc}"
            c.save(update_fields=["divergencia", "atualizado_em"])
            log.warning("Boleto %s não atualizado no banco: %s", c.pk, exc)
            return c
        c.valor, c.vencimento, c.divergencia = valor, venc, ""
        for campo in ("nosso_numero", "linha_digitavel", "codigo_barras", "pix_copia_cola", "pix_qrcode", "url_boleto",
                      "url_fatura"):
            novo_valor = getattr(novo, campo)
            if novo_valor:
                setattr(c, campo, novo_valor)
        c.save()
        _audit(c.instituicao, usuario, "COBRANCA_ALTERADA", "Boleto atualizado após ajuste da parcela",
               entidade_id=c.pk, aluno_id=p.aluno_id, valor=valor, vencimento=venc.isoformat())
        return c


# --------------------------------------------------------------------------- pagamento
def consultar_e_baixar(cob, usuario=None):
    """Pergunta ao banco a situação do boleto e atualiza a escola (baixa o pagamento, se houver)."""
    cfg = configuracao(cob.instituicao)
    try:
        sit = provedor_da(cfg).consultar(cob.id_externo)
    except ErroProvedor as exc:
        raise ValidationError(str(exc)) from None
    if sit.status == "PAGA":
        return baixar_cobranca(cob, valor_pago=sit.valor_pago, data=sit.data_pagamento, usuario=usuario)
    if sit.status == "CANCELADA" and cob.status == Cobranca.Status.PENDENTE:
        Cobranca.objects.filter(pk=cob.pk, status=Cobranca.Status.PENDENTE).update(status=Cobranca.Status.CANCELADA)
        cob.refresh_from_db()
    return cob


def baixar_cobranca(cob, *, valor_pago=None, data=None, usuario=None):
    """Marca o boleto como pago e, se for de uma parcela, registra o Recebimento (forma BOLETO). Idempotente."""
    with transaction.atomic():
        c = Cobranca.objects.select_for_update(of=("self",)).select_related("instituicao", "parcela").get(pk=cob.pk)
        if c.status == Cobranca.Status.PAGA:
            return c
        hoje = data or timezone.localdate()
        pago = Decimal(valor_pago) if valor_pago is not None else c.valor
        c.status, c.pago_em, c.valor_pago = Cobranca.Status.PAGA, hoje, pago
        if c.eh_teste or c.parcela_id is None:
            c.save()
            _audit(c.instituicao, usuario, "COBRANCA_TESTE_CONFIRMADA", "Recebimento do boleto de teste confirmado",
                   entidade_id=c.pk, valor=pago)
            return c
        p = Parcela.objects.select_for_update(of=("self",)).select_related("aluno", "contrato", "instituicao").get(pk=c.parcela_id)
        c.save()
        if p.status in (Parcela.Status.QUITADA, Parcela.Status.CANCELADA):
            c.divergencia = (f"Pagamento de R$ {pago} recebido no banco, mas a parcela já estava "
                             f"{p.get_status_display().lower()}. Conferir devolução.")
            c.save(update_fields=["divergencia", "atualizado_em"])
            _audit(c.instituicao, usuario, "COBRANCA_DIVERGENCIA", c.divergencia, entidade_id=c.pk, aluno_id=p.aluno_id)
            return c
        devido = p.valor_original - p.desconto + fin.acrescimo_atual(p, hoje) - p.valor_pago
        aplicar = min(devido, pago)
        try:
            with transaction.atomic():
                rec, _ = fin.registrar_recebimento(aluno=p.aluno, itens=[(p.pk, aplicar)], forma=Recebimento.Forma.BOLETO,
                                                   valor_recebido=None, usuario=None, token=c.token,
                                                   referencia=(c.nosso_numero or c.id_externo)[:60], hoje=hoje)
        except ValidationError as exc:
            c.divergencia = f"Pagamento de R$ {pago} recebido, mas não foi possível baixar a parcela: {exc.messages[0]}"
            c.save(update_fields=["divergencia", "atualizado_em"])
            _audit(c.instituicao, usuario, "COBRANCA_DIVERGENCIA", c.divergencia, entidade_id=c.pk, aluno_id=p.aluno_id)
            return c
        rec.operador_nome = f"Banco ({c.get_provedor_display()})"
        rec.save(update_fields=["operador_nome"])
        c.recebimento = rec
        if pago > aplicar:
            c.divergencia = (f"O banco informou R$ {pago}; foi aplicado R$ {aplicar} à parcela. "
                             "Conferir a diferença (juros/multa ou valor pago a mais).")
        elif aplicar < devido:
            c.divergencia = (f"Pagamento de R$ {pago} menor que o devido (R$ {devido}): a parcela ficou parcialmente "
                             "paga. Conferir com o pagador/banco.")
        c.save(update_fields=["recebimento", "divergencia", "atualizado_em"])
        _audit(c.instituicao, usuario, "COBRANCA_PAGA", f"Boleto pago — recebimento {rec.numero}", entidade_id=c.pk,
               aluno_id=p.aluno_id, valor=pago, recebimento=rec.pk, divergencia=bool(c.divergencia))
        return c


# --------------------------------------------------------------------------- avisos do banco (webhook)
EVENTOS_PAGAMENTO = {"PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"}
EVENTOS_CANCELAMENTO = {"PAYMENT_DELETED", "PAYMENT_BANK_SLIP_CANCELLED"}
EVENTOS_ESTORNO = {"PAYMENT_REFUNDED", "PAYMENT_RECEIVED_IN_CASH_UNDONE", "PAYMENT_CHARGEBACK_REQUESTED"}


def processar_webhook(cfg, payload):
    """Processa um aviso do banco. Idempotente pelo id do evento. Falha do banco levanta ErroProvedor (retentativa)."""
    evento_id, tipo = str(payload.get("id") or ""), str(payload.get("event") or "")
    pg = payload.get("payment") or {}
    if not evento_id or not tipo:
        raise ValidationError("Evento inválido.")
    ev, criado = EventoBanco.objects.get_or_create(
        provedor=cfg.provedor, evento_id=evento_id,
        defaults=dict(instituicao=cfg.instituicao, tipo=tipo, id_externo=str(pg.get("id") or ""),
                      dados={k: pg.get(k) for k in ("status", "value", "paymentDate", "dueDate") if k in pg}))
    if ev.instituicao_id != cfg.instituicao_id:
        return "ignorado"
    if ev.processado:
        return "duplicado"
    cob = Cobranca.objects.filter(instituicao=cfg.instituicao, provedor=cfg.provedor,
                                  id_externo=ev.id_externo).first() if ev.id_externo else None
    if cob is None:
        resultado = "cobrança desconhecida"
    elif tipo in EVENTOS_PAGAMENTO:
        try:
            cob = consultar_e_baixar(cob)  # confirma direto no banco antes de dar baixa
        except ValidationError as exc:
            raise ErroProvedor(exc.messages[0]) from None
        resultado = f"cobrança {cob.get_status_display().lower()}"
    elif tipo in EVENTOS_CANCELAMENTO:
        if cob.status == Cobranca.Status.PENDENTE:
            cob.status = Cobranca.Status.CANCELADA
            cob.save(update_fields=["status", "atualizado_em"])
        resultado = "cancelada pelo banco"
    elif tipo in EVENTOS_ESTORNO:
        cob.divergencia = f"O banco informou {tipo}. Conferir o pagamento/estorno e ajustar o recebimento, se preciso."
        cob.save(update_fields=["divergencia", "atualizado_em"])
        _audit(cob.instituicao, None, "COBRANCA_DIVERGENCIA", cob.divergencia, entidade_id=cob.pk)
        resultado = "divergência registrada"
    else:
        resultado = "evento ignorado"
    ev.processado, ev.resultado = True, resultado[:200]
    ev.save(update_fields=["processado", "resultado"])
    return resultado


def conciliar(instituicao, usuario=None):
    """Consulta no banco todos os boletos pendentes da escola (rede de segurança para avisos perdidos)."""
    pagos = 0
    for c in Cobranca.objects.filter(instituicao=instituicao, status=Cobranca.Status.PENDENTE):
        try:
            if consultar_e_baixar(c, usuario).status == Cobranca.Status.PAGA:
                pagos += 1
        except ValidationError as exc:
            log.warning("Conciliação: boleto %s não consultado: %s", c.pk, exc.messages[0])
    return pagos
