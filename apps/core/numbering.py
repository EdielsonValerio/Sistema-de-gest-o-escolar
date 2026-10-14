"""Geração segura de números (código do aluno, matrícula, recebimento).

O formato é composto por substituição de variáveis de uma *whitelist*; nenhum código é
executado. O contador é incrementado dentro de transação, com bloqueio de linha
(`select_for_update`, efetivo no PostgreSQL) e restrição única como rede de segurança.
"""
import re
from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F

from .models import (ConfiguracaoNumeracao, EscopoNumeracao, SequenciaNumeracao, TipoNumeracao)

VARIAVEIS = {
    "AnoLetivo": "Ano letivo da turma",
    "AnoCadastro": "Ano do cadastro/emissão",
    "NumeroSerie": "Número da série (ordem)",
    "NumeroTurma": "Número da turma",
    "Sequencial": "Sequencial (obrigatório)",
    "CodigoAluno": "Código único do aluno",
}

VARIAVEIS_POR_TIPO = {
    TipoNumeracao.CODIGO_ALUNO: {"AnoCadastro", "Sequencial"},
    TipoNumeracao.MATRICULA_OFICIAL: set(VARIAVEIS),
    TipoNumeracao.INSCRICAO_NAO_OFICIAL: set(VARIAVEIS),
    TipoNumeracao.RECEBIMENTO: {"AnoCadastro", "Sequencial"},
}

FORMATOS_PADRAO = {
    TipoNumeracao.CODIGO_ALUNO: ("[AnoCadastro][Sequencial]", "", 6, EscopoNumeracao.ANO_LETIVO),
    TipoNumeracao.MATRICULA_OFICIAL: ("[AnoLetivo][NumeroSerie][NumeroTurma][Sequencial]", "", 3,
                                      EscopoNumeracao.TURMA),
    TipoNumeracao.INSCRICAO_NAO_OFICIAL: ("[AnoLetivo][NumeroTurma][Sequencial]", "", 3, EscopoNumeracao.TURMA),
    TipoNumeracao.RECEBIMENTO: ("[AnoCadastro][Sequencial]", "-", 6, EscopoNumeracao.ANO_LETIVO),
}

_TOKEN = re.compile(r"\[([^\[\]]*)\]")


def _tokens(formato):
    """Divide o formato em [('lit', texto) | ('var', nome)]."""
    partes, pos = [], 0
    for m in _TOKEN.finditer(formato):
        if m.start() > pos:
            partes.append(("lit", formato[pos:m.start()]))
        partes.append(("var", m.group(1)))
        pos = m.end()
    if pos < len(formato):
        partes.append(("lit", formato[pos:]))
    return partes


def validar_formato(formato, tipo):
    permitidas = VARIAVEIS_POR_TIPO[tipo]
    formato = (formato or "").strip()
    if not formato:
        raise ValidationError("Informe o formato.")
    if "[" in re.sub(_TOKEN, "", formato) or "]" in re.sub(_TOKEN, "", formato):
        raise ValidationError("Colchetes mal formados no formato.")
    usadas = [n for k, n in _tokens(formato) if k == "var"]
    desconhecidas = [n for n in usadas if n not in VARIAVEIS]
    if desconhecidas:
        raise ValidationError(f"Variável(is) desconhecida(s): {', '.join(sorted(set(desconhecidas)))}.")
    fora = [n for n in usadas if n not in permitidas]
    if fora:
        raise ValidationError(
            f"Variável(is) não disponível(is) para este tipo: {', '.join(sorted(set(fora)))}.")
    if "Sequencial" not in usadas:
        raise ValidationError("O formato deve conter [Sequencial].")
    if usadas.count("Sequencial") > 1:
        raise ValidationError("[Sequencial] deve aparecer uma única vez.")
    literais = "".join(t for k, t in _tokens(formato) if k == "lit")
    if not re.fullmatch(r"[A-Za-z0-9\-_./]*", literais):
        raise ValidationError("Textos fixos só podem conter letras, números e - _ . /")
    return formato


def renderizar(formato, valores, separador="", tamanho=4):
    """Substitui as variáveis (whitelist). `valores` contém strings já prontas; Sequencial é int."""
    saida, anterior_var = [], False
    for kind, texto in _tokens(formato):
        if kind == "lit":
            saida.append(texto)
            anterior_var = False
            continue
        if texto not in VARIAVEIS:
            raise ValidationError(f"Variável desconhecida: {texto}")
        if anterior_var and separador:
            saida.append(separador)
        valor = valores.get(texto, "")
        if texto == "Sequencial":
            valor = str(int(valor)).zfill(tamanho)
        saida.append(str(valor))
        anterior_var = True
    return "".join(saida)


def prever(formato, tipo, separador="", tamanho=4):
    """Exemplos para a prévia na tela de configuração."""
    validar_formato(formato, tipo)
    ano = date.today().year
    exemplos = []
    for seq, serie, turma in ((1, "01", "01"), (2, "01", "02"), (37, "05", "03")):
        exemplos.append(renderizar(formato, {
            "AnoLetivo": ano, "AnoCadastro": ano, "NumeroSerie": serie, "NumeroTurma": turma,
            "Sequencial": seq, "CodigoAluno": f"{ano}000001"}, separador, tamanho))
    return exemplos


def chave_escopo(escopo, *, ano_letivo=None, serie=None, turma=None, ano_cadastro=None):
    if escopo == EscopoNumeracao.INSTITUICAO:
        return "GLOBAL"
    if escopo == EscopoNumeracao.ANO_LETIVO:
        return f"ANO:{ano_letivo if ano_letivo is not None else ano_cadastro}"
    if escopo == EscopoNumeracao.SERIE:
        return f"ANO:{ano_letivo}|SERIE:{serie}"
    if escopo == EscopoNumeracao.TURMA:
        return f"TURMA:{turma}"
    raise ValueError(escopo)


def proxima_sequencia(instituicao, tipo, chave):
    """Incrementa e devolve o próximo valor. Seguro para concorrência."""
    filtro = dict(instituicao=instituicao, tipo=tipo, chave_escopo=chave)
    with transaction.atomic():
        seq = SequenciaNumeracao.objects.select_for_update().filter(**filtro).first()
        if seq is None:
            try:
                with transaction.atomic():
                    seq = SequenciaNumeracao.objects.create(**filtro, ultimo_valor=0)
            except IntegrityError:
                seq = SequenciaNumeracao.objects.select_for_update().get(**filtro)
        SequenciaNumeracao.objects.filter(pk=seq.pk).update(ultimo_valor=F("ultimo_valor") + 1)
        return SequenciaNumeracao.objects.get(pk=seq.pk).ultimo_valor


def obter_configuracao(instituicao, tipo):
    """Configuração do tipo; cria a padrão se não existir (seguro contra criação concorrente)."""
    cfg = ConfiguracaoNumeracao.objects.filter(instituicao=instituicao, tipo=tipo).first()
    if cfg is not None:
        return cfg
    formato, sep, tam, escopo = FORMATOS_PADRAO[tipo]
    try:
        with transaction.atomic():
            return ConfiguracaoNumeracao.objects.create(
                instituicao=instituicao, tipo=tipo, formato=formato, separador=sep,
                tamanho_sequencial=tam, escopo=escopo, ativa=tipo != TipoNumeracao.INSCRICAO_NAO_OFICIAL)
    except IntegrityError:
        return ConfiguracaoNumeracao.objects.get(instituicao=instituicao, tipo=tipo)


def gerar_codigo_aluno(instituicao, ano_cadastro=None):
    ano = ano_cadastro or date.today().year
    cfg = obter_configuracao(instituicao, TipoNumeracao.CODIGO_ALUNO)
    chave = f"ANO:{ano}" if cfg.escopo != EscopoNumeracao.INSTITUICAO else "GLOBAL"
    seq = proxima_sequencia(instituicao, TipoNumeracao.CODIGO_ALUNO, chave)
    return renderizar(cfg.formato, {"AnoCadastro": ano, "Sequencial": seq}, cfg.separador, cfg.tamanho_sequencial)


def gerar_numero_recebimento(instituicao, ano=None):
    ano = ano or date.today().year
    cfg = obter_configuracao(instituicao, TipoNumeracao.RECEBIMENTO)
    seq = proxima_sequencia(instituicao, TipoNumeracao.RECEBIMENTO, f"ANO:{ano}")
    return renderizar(cfg.formato, {"AnoCadastro": ano, "Sequencial": seq}, cfg.separador, cfg.tamanho_sequencial)


def gerar_numero_matricula(instituicao, *, turma, aluno):
    """Devolve o número da matrícula/inscrição ou None se a numeração não se aplica."""
    from apps.academic.models import Turma  # import tardio (evita ciclo)

    if not turma.exige_numero_matricula:
        return None
    oficial = turma.tipo == Turma.Tipo.OFICIAL
    tipo = TipoNumeracao.MATRICULA_OFICIAL if oficial else TipoNumeracao.INSCRICAO_NAO_OFICIAL
    cfg = obter_configuracao(instituicao, tipo)
    if not cfg.ativa:
        return None
    ano_letivo = turma.ano_letivo.ano if oficial else turma.ano_referencia
    serie_ordem = turma.serie_ofertada.serie_referencia.ordem if oficial else 0
    chave = chave_escopo(
        cfg.escopo, ano_letivo=ano_letivo,
        serie=turma.serie_ofertada_id if oficial else None, turma=turma.pk)
    seq = proxima_sequencia(instituicao, tipo, chave)
    return renderizar(cfg.formato, {
        "AnoLetivo": ano_letivo,
        "AnoCadastro": aluno.criado_em.year,
        "NumeroSerie": str(serie_ordem).zfill(2),
        "NumeroTurma": str(turma.numero).zfill(2),
        "Sequencial": seq,
        "CodigoAluno": aluno.codigo,
    }, cfg.separador, cfg.tamanho_sequencial)


_OBRIGATORIAS_POR_ESCOPO = {
    EscopoNumeracao.INSTITUICAO: set(),
    EscopoNumeracao.ANO_LETIVO: {"AnoLetivo"},
    EscopoNumeracao.SERIE: {"AnoLetivo", "NumeroSerie"},
    EscopoNumeracao.TURMA: {"AnoLetivo", "NumeroSerie", "NumeroTurma"},
}


def validar_escopo(formato, tipo, escopo):
    """Garante que o formato distingue os números dentro do escopo (evita colisões)."""
    if tipo in (TipoNumeracao.CODIGO_ALUNO, TipoNumeracao.RECEBIMENTO):
        if escopo not in (EscopoNumeracao.INSTITUICAO, EscopoNumeracao.ANO_LETIVO):
            raise ValidationError("Para este tipo, o escopo deve ser Instituição ou Ano.")
        if escopo == EscopoNumeracao.ANO_LETIVO and "[AnoCadastro]" not in formato:
            raise ValidationError("Com escopo anual, o formato deve conter [AnoCadastro].")
        return
    exigidas = set(_OBRIGATORIAS_POR_ESCOPO[escopo])
    if tipo == TipoNumeracao.INSCRICAO_NAO_OFICIAL:
        exigidas.discard("NumeroSerie")  # turmas não oficiais não têm série
    usadas = {n for k, n in _tokens(formato) if k == "var"}
    faltam = sorted(exigidas - usadas)
    if faltam:
        raise ValidationError(
            "Para o escopo escolhido, o formato precisa conter: " + ", ".join(f"[{v}]" for v in faltam) + ".")
