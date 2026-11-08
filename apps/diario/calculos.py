"""Frequência e médias (regras configuráveis em ConfiguracaoDiario)."""
from decimal import ROUND_HALF_UP, Decimal

from .models import Aula, Avaliacao, Frequencia, Nota
from .servicos import configuracao

DOIS = Decimal("0.01")
DEZ = Decimal("10")


def _q(v):
    return v.quantize(DOIS, rounding=ROUND_HALF_UP)


def frequencia_da_alocacao(alocacao):
    """{matricula_id: (percentual|None, aulas, faltas)}. Só aulas FINALIZADAS contam; falta justificada é abonada."""
    aulas = {a.pk: a.quantidade for a in Aula.objects.filter(alocacao=alocacao, status=Aula.Status.FINALIZADA)}
    total, faltas = {}, {}
    for f in Frequencia.objects.filter(aula_id__in=list(aulas)):
        total[f.matricula_id] = total.get(f.matricula_id, 0) + aulas[f.aula_id]
        if f.situacao == Frequencia.Situacao.FALTA:
            faltas[f.matricula_id] = faltas.get(f.matricula_id, 0) + aulas[f.aula_id]
    out = {}
    for mid, t in total.items():
        fa = faltas.get(mid, 0)
        out[mid] = (_q(Decimal(t - fa) * 100 / Decimal(t)), t, fa)
    return out


def media_periodo(avaliacoes, notas_por_avaliacao, matricula_id, usa_recuperacao=True):
    """Média 0-10 do período: Σ(nota/nota_máx×10 × peso)/Σ peso; a recuperação substitui a média se for maior."""
    soma = peso = Decimal("0")
    rec = None
    for av in avaliacoes:
        valor = notas_por_avaliacao.get((av.pk, matricula_id))
        if valor is None:
            continue
        norm = valor / av.nota_maxima * DEZ
        if av.tipo == Avaliacao.Tipo.RECUPERACAO:
            rec = norm if rec is None else max(rec, norm)
        elif av.tipo == Avaliacao.Tipo.NORMAL:
            soma += norm * av.peso
            peso += av.peso
    media = soma / peso if peso else None
    if usa_recuperacao and rec is not None:
        media = rec if media is None else max(media, rec)
    return _q(media) if media is not None else None


def desempenho_da_alocacao(alocacao):
    """Lista de dicts por aluno: médias por período, média final, frequência e situação."""
    from .servicos import matriculas_da_turma, modelo_da_alocacao, periodos_da_alocacao
    modelo = modelo_da_alocacao(alocacao)
    if modelo is not None:  # regra parametrizada pelo administrador do sistema (apps/diario/motor.py)
        from . import motor
        return motor.calcular_alocacao(alocacao, modelo)
    cfg = configuracao(alocacao.instituicao)
    periodos = list(periodos_da_alocacao(alocacao))
    avs = list(Avaliacao.objects.filter(alocacao=alocacao))
    notas = {(n.avaliacao_id, n.matricula_id): n.valor for n in Nota.objects.filter(avaliacao__in=avs)}
    freq = frequencia_da_alocacao(alocacao)
    linhas = []
    for m in matriculas_da_turma(alocacao.turma):
        medias = [media_periodo([a for a in avs if a.periodo_id == p.pk], notas, m.pk, cfg.usa_recuperacao) for p in periodos]
        validas = [x for x in medias if x is not None]
        final = _q(sum(validas) / len(validas)) if validas else None
        pct, aulas, faltas = freq.get(m.pk, (None, 0, 0))
        if pct is not None and pct < cfg.frequencia_minima:
            sit = "Frequência insuficiente"
        elif final is None:
            sit = "Sem notas"
        elif final >= cfg.media_aprovacao:
            sit = "Aprovado"
        else:
            sit = "Abaixo da média"
        linhas.append({"matricula": m, "aluno": m.aluno, "medias": medias, "media_final": final, "frequencia": pct,
                       "aulas": aulas, "faltas": faltas, "situacao": sit,
                       "baixa_frequencia": pct is not None and pct < cfg.frequencia_minima})
    return periodos, linhas
