"""Motor de cálculo do Modelo de Avaliação (fases, componentes, recuperações, média anual e situação).

`fechar` e `nota_da_fase` são funções PURAS (usadas também pelo simulador do administrador). `calcular_alocacao` lê as notas
reais do diário e produz as linhas de desempenho no mesmo formato da regra padrão (calculos.desempenho_da_alocacao).
"""
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from . import formulas
from .models import Avaliacao, ComponenteNota, ModeloAvaliacao, Nota

ZERO = Decimal(0)
FORMULA_REC_FINAL_PADRAO = "MAX(MF, (MF + RF) / 2)"
_MODOS = {"COMERCIAL": ROUND_HALF_UP, "CIMA": ROUND_CEILING, "BAIXO": ROUND_FLOOR}


def q(modelo, v):
    """Arredonda conforme o modelo (casas decimais e modo)."""
    return v.quantize(Decimal(1).scaleb(-modelo.casas_decimais), rounding=_MODOS.get(modelo.arredondamento, ROUND_HALF_UP))


def nomes_formula_fase(componentes):
    return [c.sigla for c in componentes] + ["MAXIMA"]


def nomes_formula_final(n_fases):
    return [f"B{i}" for i in range(1, n_fases + 1)] + ["MA", "MP", "SOMA", "N", "MAXIMA"]


NOMES_REC_FINAL = ["MF", "RF", "MA", "MP", "MAXIMA"]


def agregar(componente, itens):
    """Valor do componente (0..valor_maximo) a partir de [(nota, nota_maxima, peso)] das avaliações; None se não há notas."""
    if not itens:
        return None
    vmax, ag = componente.valor_maximo, componente.agregacao
    if ag == ComponenteNota.Agregacao.SOMA:
        return min(sum((n for n, _, _ in itens), ZERO), vmax)
    fracoes = [(n / m, p) for n, m, p in itens]
    if ag == ComponenteNota.Agregacao.PONDERADA:
        return sum((f * p for f, p in fracoes), ZERO) / sum((p for _, p in fracoes), ZERO) * vmax
    solo = [f for f, _ in fracoes]
    if ag == ComponenteNota.Agregacao.MAIOR:
        return max(solo) * vmax
    if ag == ComponenteNota.Agregacao.DESC_MENOR and len(solo) > 1:
        solo = sorted(solo)[1:]
    return sum(solo, ZERO) / len(solo) * vmax


def nota_da_fase(modelo, fase, componentes, valores):
    """Nota da fase a partir dos valores dos componentes {sigla: Decimal|None}. None se nenhum componente tem nota."""
    if all(valores.get(c.sigla) is None for c in componentes):
        return None
    vars_ = {c.sigla: valores.get(c.sigla) or ZERO for c in componentes}
    vars_["MAXIMA"] = modelo.nota_maxima
    expr = fase.formula or " + ".join(c.sigla for c in componentes)
    r = formulas.avaliar(expr, vars_)
    return q(modelo, min(max(r, ZERO), modelo.nota_maxima))


def aplicar_rec_parcial(modelo, nota, rp):
    if rp is None:
        return nota
    if nota is None:
        return rp
    regra = modelo.regra_rec_parcial
    if regra == ModeloAvaliacao.RegraRecParcial.SUBSTITUI:
        return rp
    if regra == ModeloAvaliacao.RegraRecParcial.MEDIA:
        return (nota + rp) / 2
    return max(nota, rp)


def fechar(modelo, fases, brutas, rps, rf, freq):
    """Resultado do aluno. `brutas`/`rps`: uma nota (ou None) por fase; `rf`: recuperação final; `freq`: % ou None."""
    B = []
    for b, rp in zip(brutas, rps):
        v = aplicar_rec_parcial(modelo, b, rp) if modelo.usa_rec_parcial else b
        B.append(q(modelo, v) if v is not None else None)
    disp = [(f, b) for f, b in zip(fases, B) if b is not None]
    completa = bool(fases) and len(disp) == len(fases)
    pesos = sum((f.peso for f, _ in disp), ZERO)
    mp = sum((b * f.peso for f, b in disp), ZERO) / pesos if pesos else None
    ma = sum((b for _, b in disp), ZERO) / len(disp) if disp else None
    baixa = freq is not None and freq < modelo.frequencia_minima
    res = {"medias": B, "completa": completa, "media_anual": None, "media_final": q(modelo, mp) if mp is not None else None,
           "rec_final": rf, "baixa_frequencia": baixa, "parcial": not completa}
    if not disp:
        res["situacao"] = "Sem notas"
        return res
    if not completa:
        res["situacao"] = "Frequência insuficiente" if baixa else "Em andamento"
        return res
    vars_ = {f"B{i}": b for i, b in enumerate(B, 1)}
    vars_.update(MA=ma, MP=mp, SOMA=sum(B, ZERO), N=Decimal(len(B)), MAXIMA=modelo.nota_maxima)
    mf = q(modelo, max(formulas.avaliar(modelo.formula_final or "MP", vars_), ZERO))
    res.update(media_anual=mf, media_final=mf, parcial=False)
    if baixa:
        res["situacao"] = "Reprovado por frequência"
    elif mf >= modelo.media_aprovacao:
        res["situacao"] = "Aprovado"
    elif modelo.usa_rec_final:
        if rf is None:
            res["situacao"] = "Recuperação final"
        else:
            vf = {"MF": mf, "RF": rf, "MA": ma, "MP": mp, "MAXIMA": modelo.nota_maxima}
            mfr = q(modelo, max(formulas.avaliar(modelo.formula_rec_final or FORMULA_REC_FINAL_PADRAO, vf), ZERO))
            minimo = modelo.media_aprovacao_rec_final if modelo.media_aprovacao_rec_final is not None else modelo.media_aprovacao
            res["media_final"] = mfr
            res["situacao"] = "Aprovado na recuperação final" if mfr >= minimo else "Reprovado"
    else:
        res["situacao"] = "Reprovado"
    return res


def _escala(modelo, av, valor):
    return valor / av.nota_maxima * modelo.nota_maxima


def calcular_alocacao(alocacao, modelo):
    """(fases, linhas) com as notas reais do diário. Mesma forma de calculos.desempenho_da_alocacao."""
    from .calculos import frequencia_da_alocacao
    from .servicos import matriculas_da_turma
    fases = list(modelo.fases.prefetch_related("componentes").order_by("ordem"))
    avs = list(Avaliacao.objects.filter(alocacao=alocacao))
    notas = {(n.avaliacao_id, n.matricula_id): n.valor for n in Nota.objects.filter(avaliacao__in=avs) if n.valor is not None}
    freq = frequencia_da_alocacao(alocacao)
    linhas = []
    for m in matriculas_da_turma(alocacao.turma):
        brutas, rps = [], []
        for fase in fases:
            comps = list(fase.componentes.all())
            da_fase = [a for a in avs if a.periodo_id == fase.pk]
            normais = [a for a in da_fase if a.tipo == Avaliacao.Tipo.NORMAL]
            if comps:
                valores = {c.sigla: agregar(c, [(notas[(a.pk, m.pk)], a.nota_maxima, a.peso) for a in normais
                                                if a.componente_id == c.pk and (a.pk, m.pk) in notas]) for c in comps}
                brutas.append(nota_da_fase(modelo, fase, comps, valores))
            else:
                itens = [(_escala(modelo, a, notas[(a.pk, m.pk)]), a.peso) for a in normais if (a.pk, m.pk) in notas]
                pesos = sum((p for _, p in itens), ZERO)
                brutas.append(q(modelo, sum((v * p for v, p in itens), ZERO) / pesos) if pesos else None)
            recs = [_escala(modelo, a, notas[(a.pk, m.pk)]) for a in da_fase
                    if a.tipo == Avaliacao.Tipo.RECUPERACAO and (a.pk, m.pk) in notas]
            rps.append(max(recs) if recs else None)
        rfs = [_escala(modelo, a, notas[(a.pk, m.pk)]) for a in avs if a.tipo == Avaliacao.Tipo.REC_FINAL and (a.pk, m.pk) in notas]
        rf = max(rfs) if rfs and modelo.usa_rec_final else None
        pct, aulas, faltas = freq.get(m.pk, (None, 0, 0))
        r = fechar(modelo, fases, brutas, rps, q(modelo, rf) if rf is not None else None, pct)
        r.update(matricula=m, aluno=m.aluno, frequencia=pct, aulas=aulas, faltas=faltas, rec_parciais=rps, notas_fase=brutas)
        linhas.append(r)
    return fases, linhas
