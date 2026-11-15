"""Boletim e histórico escolar.

- `sincronizar_alocacao`: rotina que grava (ou apaga) as notas liberadas ao boletim sempre que um diário é fechado
  ou reaberto. Uma fase só aparece no boletim depois que o professor fecha o diário dela.
- `dados_boletim` / `dados_historico`: montam o que os modelos de documento recebem.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import escape

from apps.core.models import ConfiguracaoGeral
from apps.enrollments.models import Matricula

from . import calculos, servicos
from .models import (AlocacaoProfessor, FechamentoDiario, HistoricoExterno, ResultadoFase, ResultadoFinal)

VAZIO = "—"
REPROVADOS = {"Reprovado", "Reprovado por frequência", "Abaixo da média", "Frequência insuficiente"}


def _num(v, casas=2):
    if v is None:
        return VAZIO
    return f"{Decimal(v).quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP):.{casas}f}".replace(".", ",")


def classificar(situacao):
    if (situacao or "").startswith("Aprovado"):
        return ResultadoFinal.Resultado.APROVADO
    if situacao in REPROVADOS:
        return ResultadoFinal.Resultado.REPROVADO
    return ResultadoFinal.Resultado.PENDENTE


# --------------------------------------------------------------------------- rotina de liberação
@transaction.atomic
def sincronizar_alocacao(alocacao):
    """Grava os resultados das fases com diário fechado e, se todas estiverem fechadas, o resultado final.

    Idempotente: pode ser chamada a qualquer momento (o comando `sincronizar_boletins` faz isso para todas)."""
    periodos = list(servicos.periodos_da_alocacao(alocacao))
    fechados = set(FechamentoDiario.objects.filter(alocacao=alocacao, fechado=True, periodo__in=periodos)
                   .values_list("periodo_id", flat=True))
    ResultadoFase.objects.filter(alocacao=alocacao).exclude(periodo_id__in=fechados).delete()
    todas = bool(periodos) and len(fechados) == len(periodos)
    if not fechados:
        ResultadoFinal.objects.filter(alocacao=alocacao).delete()
        return
    ps, linhas = calculos.desempenho_da_alocacao(alocacao)
    carga = alocacao.disciplina.carga_horaria
    for l in linhas:
        m = l["matricula"]
        for i, p in enumerate(ps):
            if p.pk in fechados:
                ResultadoFase.objects.update_or_create(
                    matricula=m, alocacao=alocacao, periodo=p,
                    defaults={"instituicao": alocacao.instituicao, "nota": l["medias"][i], "frequencia": l["frequencia"]})
        if todas:
            ResultadoFinal.objects.update_or_create(
                matricula=m, alocacao=alocacao,
                defaults={"instituicao": alocacao.instituicao, "media_final": l["media_final"],
                          "frequencia": l["frequencia"], "aulas": l["aulas"], "faltas": l["faltas"],
                          "situacao": l["situacao"], "resultado": classificar(l["situacao"]), "carga_horaria": carga})
        else:
            ResultadoFinal.objects.filter(matricula=m, alocacao=alocacao).delete()


def _alocacoes(matricula):
    return list(AlocacaoProfessor.objects.filter(instituicao=matricula.instituicao, turma=matricula.turma)
                .select_related("disciplina", "ano_letivo", "turma").order_by("disciplina__nome", "id"))


def garantir_sincronia(matricula):
    """Preenche lacunas (ex.: aluno que entrou na turma depois do fechamento de uma fase)."""
    for a in _alocacoes(matricula):
        fechadas = FechamentoDiario.objects.filter(alocacao=a, fechado=True).count()
        if fechadas and ResultadoFase.objects.filter(alocacao=a, matricula=matricula).count() < fechadas:
            sincronizar_alocacao(a)


# --------------------------------------------------------------------------- boletim
def dados_boletim(matricula):
    garantir_sincronia(matricula)
    alocs = _alocacoes(matricula)
    fases, criterio = [], None
    for a in alocs:
        if not fases:
            fases = list(servicos.periodos_da_alocacao(a))
        criterio = criterio or servicos.criterios_da_alocacao(a)
    rfs = {}
    for r in ResultadoFase.objects.filter(matricula=matricula).select_related("periodo"):
        rfs.setdefault(r.alocacao_id, {})[r.periodo_id] = r
    rfin = {r.alocacao_id: r for r in ResultadoFinal.objects.filter(matricula=matricula)}

    por_disc = {}
    for a in alocs:  # mesma disciplina com professores diferentes: vale o resultado mais recente de cada fase
        por_disc.setdefault(a.disciplina_id, []).append(a)
    linhas = []
    for lista in por_disc.values():
        notas, ultima, final = {}, None, None
        for a in lista:
            for pid, r in rfs.get(a.pk, {}).items():
                notas[pid] = r.nota
                if ultima is None or r.periodo.ordem >= ultima[0]:
                    ultima = (r.periodo.ordem, r.frequencia)
            final = rfin.get(a.pk) or final
        freq = ultima[1] if ultima else None
        linhas.append({"disciplina": lista[0].disciplina, "notas": [notas.get(p.pk) if p.pk in notas else False
                                                                    for p in fases],
                       "final": final, "frequencia": final.frequencia if final else freq})
    liberadas = sum(1 for i, _ in enumerate(fases) if any(l["notas"][i] is not False for l in linhas))
    completo = bool(linhas) and all(l["final"] is not None for l in linhas)
    if completo:
        rs = {l["final"].resultado for l in linhas}
        geral = ("Reprovado" if ResultadoFinal.Resultado.REPROVADO in rs
                 else "Pendente" if ResultadoFinal.Resultado.PENDENTE in rs else "Aprovado")
    else:
        geral = None
    medias = [l["final"].media_final for l in linhas if l["final"] and l["final"].media_final is not None]
    freqs = [l["frequencia"] for l in linhas if l["frequencia"] is not None]
    serie = getattr(getattr(matricula.turma, "serie_ofertada", None), "serie_referencia", None)
    return {
        "matricula": matricula, "aluno": matricula.aluno, "ano": matricula.ano_letivo.ano if matricula.ano_letivo else None,
        "serie": serie.nome if serie else VAZIO, "turma": matricula.turma.nome, "fases": fases, "linhas": linhas,
        "liberadas": liberadas, "completo": completo, "resultado": geral,
        "media_geral": sum(medias) / len(medias) if completo and medias else None,
        "frequencia_geral": sum(freqs) / len(freqs) if freqs else None,
        "nota_minima": criterio[0] if criterio else None, "frequencia_minima": criterio[1] if criterio else None,
    }


def tabela_boletim_html(d):
    cab = "".join(f'<th class="c">{escape(p.nome)}</th>' for p in d["fases"])
    corpo = []
    for l in d["linhas"]:
        celulas = "".join(f'<td class="c">{VAZIO if n is False else _num(n)}</td>' for n in l["notas"])
        f = l["final"]
        corpo.append(f'<tr><td>{escape(l["disciplina"].nome)}</td>{celulas}'
                     f'<td class="c">{_num(f.media_final) if f else VAZIO}</td>'
                     f'<td class="c">{_num(l["frequencia"], 1) + "%" if l["frequencia"] is not None else VAZIO}</td>'
                     f'<td>{escape(f.situacao) if f else "Em andamento"}</td></tr>')
    return (f'<table class="doc-tabela"><thead><tr><th>Disciplina</th>{cab}<th class="c">Média final</th>'
            f'<th class="c">Frequência</th><th>Situação</th></tr></thead><tbody>{"".join(corpo)}</tbody></table>')


def liberar_boletim(matricula):
    d = dados_boletim(matricula)
    if not d["linhas"]:
        raise ValidationError("A turma ainda não tem disciplinas com professor alocado.")
    if not d["liberadas"]:
        raise ValidationError("O boletim fica disponível depois que o professor concluir (fechar) a primeira fase avaliativa.")
    return d


def variaveis_boletim(d):
    from apps.documents.variaveis import HtmlSeguro
    m = d["matricula"]
    return {
        "tabelaBoletim": HtmlSeguro(tabela_boletim_html(d)),
        "resultadoFinal": d["resultado"] or "Em andamento",
        "mediaGeral": _num(d["media_geral"]), "frequenciaGeral": _num(d["frequencia_geral"], 1) + "%"
        if d["frequencia_geral"] is not None else VAZIO,
        "fasesLiberadas": f"{d['liberadas']} de {len(d['fases'])}",
        "anoLetivoBoletim": str(d["ano"] or VAZIO), "serieBoletim": d["serie"], "turmaBoletim": d["turma"],
        # a matrícula do boletim (não necessariamente a atual do aluno)
        "numeroMatricula": m.numero or VAZIO, "anoLetivo": str(d["ano"] or VAZIO), "serieAluno": d["serie"],
        "turmaAluno": d["turma"], "codigoTurma": m.turma.codigo, "situacaoMatricula": m.get_status_display(),
    }


def matriculas_do_aluno(aluno):
    """Matrículas oficiais com turma, da mais recente para a mais antiga."""
    return list(aluno.matriculas.filter(tipo="OFICIAL").exclude(status=Matricula.Status.CANCELADA)
                .select_related("turma", "turma__serie_ofertada__serie_referencia", "ano_letivo")
                .order_by("-ano_letivo__ano", "-id"))


# --------------------------------------------------------------------------- histórico
def _media_parcial(d, linha):
    """Média ponderada pelos pesos das fases já liberadas (histórico parcial)."""
    soma = peso = Decimal("0")
    for p, n in zip(d["fases"], linha["notas"]):
        if n is not False and n is not None:
            soma += n * p.peso
            peso += p.peso
    return soma / peso if peso else None


def dados_historico(aluno):
    cfg = ConfiguracaoGeral.objects.filter(instituicao=aluno.instituicao).first()
    parcial_ok = bool(cfg and cfg.historico_parcial)
    blocos, omitidos = [], 0
    for m in matriculas_do_aluno(aluno):
        d = dados_boletim(m)
        if not d["linhas"] or not d["liberadas"]:
            continue
        if not d["completo"] and not parcial_ok:
            omitidos += 1
            continue
        linhas = []
        for l in d["linhas"]:
            f = l["final"]
            linhas.append({"disciplina": l["disciplina"].nome,
                           "media": f.media_final if f else _media_parcial(d, l),
                           "carga": f.carga_horaria if f else l["disciplina"].carga_horaria,
                           "frequencia": l["frequencia"],
                           "situacao": f.situacao if f else "Em andamento"})
        blocos.append({"ano": d["ano"], "serie": d["serie"], "escola": aluno.instituicao.nome, "cidade_uf": "",
                       "origem": "interna", "parcial": not d["completo"],
                       "resultado": d["resultado"] or "Em andamento", "linhas": linhas, "observacoes": ""})
    for h in HistoricoExterno.objects.filter(aluno=aluno).prefetch_related("disciplinas"):
        blocos.append({"ano": h.ano, "serie": h.serie, "escola": h.escola, "cidade_uf": h.cidade_uf,
                       "origem": "externa", "parcial": False, "resultado": h.get_resultado_display(),
                       "observacoes": h.observacoes,
                       "linhas": [{"disciplina": x.nome, "media": x.media, "carga": x.carga_horaria,
                                   "frequencia": x.frequencia, "situacao": ""} for x in h.disciplinas.all()]})
    blocos.sort(key=lambda b: (b["ano"] or 0, b["origem"] == "interna"))
    return {"aluno": aluno, "blocos": blocos, "parcial": any(b["parcial"] for b in blocos), "omitidos": omitidos,
            "parcial_permitido": parcial_ok}


def liberar_historico(aluno):
    d = dados_historico(aluno)
    if not d["blocos"]:
        if d["omitidos"]:
            raise ValidationError(
                "O histórico escolar só pode ser gerado depois que todas as fases do ano letivo forem concluídas. "
                "Para gerar histórico parcial, ative a opção em Configurações gerais.")
        raise ValidationError("Não há resultados para o histórico: nenhuma fase foi concluída e não há anos "
                              "cadastrados de outras escolas.")
    return d


def _carga_total(d):
    return sum((l["carga"] or 0) for b in d["blocos"] for l in b["linhas"])


def tabela_historico_html(d):
    partes = []
    for b in d["blocos"]:
        titulo = f'{b["ano"]} — {b["serie"]} — {b["escola"]}' + (f' ({b["cidade_uf"]})' if b["cidade_uf"] else "")
        if b["parcial"]:
            titulo += " — PARCIAL (ano em andamento)"
        linhas = "".join(
            f'<tr><td>{escape(l["disciplina"])}</td>'
            f'<td class="c">{escape(_num(l["media"]) if isinstance(l["media"], Decimal) else (l["media"] or VAZIO))}</td>'
            f'<td class="c">{l["carga"] if l["carga"] else VAZIO}</td>'
            f'<td class="c">{_num(l["frequencia"], 1) + "%" if l["frequencia"] is not None else VAZIO}</td></tr>'
            for l in b["linhas"]) or f'<tr><td colspan="4">{VAZIO}</td></tr>'
        obs = f'<div class="doc-obs">{escape(b["observacoes"])}</div>' if b["observacoes"] else ""
        partes.append(
            f'<div class="doc-tabela-titulo">{escape(titulo)}</div>'
            f'<table class="doc-tabela"><thead><tr><th>Disciplina</th><th class="c">Média final</th>'
            f'<th class="c">Carga horária</th><th class="c">Frequência</th></tr></thead><tbody>{linhas}</tbody></table>'
            f'<div class="doc-resultado">Resultado: <strong>{escape(b["resultado"])}</strong></div>{obs}')
    return "".join(partes)


def variaveis_historico(d):
    from apps.documents.variaveis import HtmlSeguro
    carga = _carga_total(d)
    anos = [b["ano"] for b in d["blocos"] if b["ano"]]
    return {
        "tabelaHistorico": HtmlSeguro(tabela_historico_html(d)),
        "situacaoHistorico": "Parcial" if d["parcial"] else "Completo",
        "cargaHorariaTotal": f"{carga} horas" if carga else VAZIO,
        "periodoHistorico": f"{min(anos)} a {max(anos)}" if anos else VAZIO,
    }
