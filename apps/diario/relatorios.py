"""Relatórios do Diário. Cada um devolve {"titulo", "colunas", "linhas"}; o escopo respeita o perfil:
coordenação/administração enxergam a escola; o professor, somente as próprias alocações."""
from django.db.models import Count, Q

from . import calculos, servicos
from .models import AlocacaoProfessor, Aula, Avaliacao, FechamentoDiario, PeriodoLetivo


def _fmt(v, suf=""):
    return "—" if v is None else f"{str(v).replace('.', ',')}{suf}"


def alocacoes_visiveis(user, escola, g):
    qs = AlocacaoProfessor.objects.filter(instituicao=escola).select_related(
        "turma", "disciplina", "professor", "professor__perfil", "ano_letivo")
    if not user.has_perm("diario.coordenar_diario"):
        qs = qs.filter(professor=user)  # professor: só as próprias
    for chave, campo in (("turma", "turma_id"), ("professor", "professor_id"), ("disciplina", "disciplina_id")):
        if str(g.get(chave, "")).isdigit():
            if chave == "professor" and not user.has_perm("diario.coordenar_diario"):
                continue
            qs = qs.filter(**{campo: g[chave]})
    return qs


def r_aulas(user, escola, g):
    aulas = Aula.objects.filter(alocacao__in=alocacoes_visiveis(user, escola, g)).select_related(
        "turma", "disciplina", "professor", "professor__perfil", "periodo")
    if g.get("de"):
        aulas = aulas.filter(data__gte=g["de"])
    if g.get("ate"):
        aulas = aulas.filter(data__lte=g["ate"])
    return {"titulo": "Aulas ministradas",
            "colunas": ["Data", "Turma", "Disciplina", "Professor", "Aulas", "Conteúdo", "Situação"],
            "linhas": [[a.data.strftime("%d/%m/%Y"), a.turma.nome, a.disciplina.nome, servicos.nome_do(a.professor),
                        a.quantidade, (a.conteudo or "")[:90], a.get_status_display()] for a in aulas]}


def _por_aluno(user, escola, g):
    for a in alocacoes_visiveis(user, escola, g):
        _, linhas = calculos.desempenho_da_alocacao(a)
        for l in linhas:
            yield a, l


def r_frequencia_turma(user, escola, g):
    linhas = []
    for a in alocacoes_visiveis(user, escola, g):
        _, ls = calculos.desempenho_da_alocacao(a)
        pcts = [l["frequencia"] for l in ls if l["frequencia"] is not None]
        linhas.append([a.turma.nome, a.disciplina.nome, servicos.nome_do(a.professor), len(ls),
                       _fmt(round(sum(pcts) / len(pcts), 1) if pcts else None, "%"),
                       sum(1 for l in ls if l["baixa_frequencia"])])
    return {"titulo": "Frequência por turma", "colunas": ["Turma", "Disciplina", "Professor", "Alunos", "Frequência média",
                                                            "Alunos com baixa frequência"], "linhas": linhas}


def r_frequencia_individual(user, escola, g):
    return {"titulo": "Frequência individual",
            "colunas": ["Aluno", "Turma", "Disciplina", "Aulas", "Faltas", "Frequência"],
            "linhas": [[l["aluno"].nome, a.turma.nome, a.disciplina.nome, l["aulas"], l["faltas"], _fmt(l["frequencia"], "%")]
                       for a, l in _por_aluno(user, escola, g)]}


def r_baixa_frequencia(user, escola, g):
    return {"titulo": "Alunos com baixa frequência (abaixo do mínimo do modelo de avaliação da turma)",
            "colunas": ["Aluno", "Turma", "Disciplina", "Aulas", "Faltas", "Frequência"],
            "linhas": [[l["aluno"].nome, a.turma.nome, a.disciplina.nome, l["aulas"], l["faltas"], _fmt(l["frequencia"], "%")]
                       for a, l in _por_aluno(user, escola, g) if l["baixa_frequencia"]]}


def r_notas_medias(user, escola, g):
    linhas = []
    for a, l in _por_aluno(user, escola, g):
        linhas.append([l["aluno"].nome, a.turma.nome, a.disciplina.nome] + [_fmt(m) for m in l["medias"]] +
                      [_fmt(l["media_final"]), l["situacao"]])
    maxp = max((len(r) - 5 for r in linhas), default=0)
    cols = ["Aluno", "Turma", "Disciplina"] + [f"Período {i + 1}" for i in range(maxp)] + ["Média final", "Situação"]
    linhas = [r[:3] + r[3:-2] + ["—"] * (maxp - (len(r) - 5)) + r[-2:] for r in linhas]
    return {"titulo": "Notas e médias", "colunas": cols, "linhas": linhas}


def r_recuperacao(user, escola, g):
    return {"titulo": "Alunos em recuperação ou abaixo da média",
            "colunas": ["Aluno", "Turma", "Disciplina", "Média final", "Frequência"],
            "linhas": [[l["aluno"].nome, a.turma.nome, a.disciplina.nome, _fmt(l["media_final"]), _fmt(l["frequencia"], "%")]
                       for a, l in _por_aluno(user, escola, g) if l["situacao"] in ("Abaixo da média", "Recuperação final", "Reprovado")]}


def r_conteudos(user, escola, g):
    aulas = Aula.objects.filter(alocacao__in=alocacoes_visiveis(user, escola, g), status=Aula.Status.FINALIZADA
                                ).select_related("turma", "disciplina").order_by("turma__nome", "disciplina__nome", "data")
    return {"titulo": "Conteúdos ministrados", "colunas": ["Turma", "Disciplina", "Data", "Conteúdo"],
            "linhas": [[a.turma.nome, a.disciplina.nome, a.data.strftime("%d/%m/%Y"), a.conteudo] for a in aulas]}


def r_desempenho_professores(user, escola, g):
    aloc = (alocacoes_visiveis(user, escola, g).annotate(
        fin=Count("aulas", filter=Q(aulas__status="FINALIZADA"), distinct=True),
        pend=Count("aulas", filter=~Q(aulas__status="FINALIZADA"), distinct=True),
        avs=Count("avaliacoes", distinct=True)))
    por = {}
    for a in aloc:
        d = por.setdefault(a.professor_id, [servicos.nome_do(a.professor), 0, 0, 0, 0])
        d[1] += 1
        d[2] += a.fin
        d[3] += a.pend
        d[4] += a.avs
    return {"titulo": "Desempenho dos professores (registros)",
            "colunas": ["Professor", "Turmas/disciplinas", "Aulas finalizadas", "Aulas pendentes", "Avaliações"],
            "linhas": sorted(por.values())}


def r_pendencias(user, escola, g):
    aloc = (alocacoes_visiveis(user, escola, g).filter(situacao="ATIVA").annotate(
        fin=Count("aulas", filter=Q(aulas__status="FINALIZADA"), distinct=True),
        pend=Count("aulas", filter=~Q(aulas__status="FINALIZADA"), distinct=True)))
    linhas = []
    for a in aloc:
        problema = ("Nenhuma aula registrada" if a.fin + a.pend == 0 else f"{a.pend} aula(s) pendente(s)" if a.pend else "")
        if problema:
            linhas.append([a.turma.nome, a.disciplina.nome, servicos.nome_do(a.professor), problema])
    return {"titulo": "Pendências dos diários", "colunas": ["Turma", "Disciplina", "Professor", "Pendência"], "linhas": linhas}


def r_fechamento(user, escola, g):
    fech = {(f.alocacao_id, f.periodo_id) for f in FechamentoDiario.objects.filter(instituicao=escola, fechado=True)}
    linhas = []
    for a in alocacoes_visiveis(user, escola, g).filter(situacao="ATIVA"):
        ps = list(servicos.periodos_da_alocacao(a))
        fechados = [p.nome for p in ps if (a.pk, p.pk) in fech]
        linhas.append([a.turma.nome, a.disciplina.nome, servicos.nome_do(a.professor), f"{len(fechados)} de {len(ps)}",
                       ", ".join(fechados) or "—"])
    return {"titulo": "Fechamento dos diários por período",
            "colunas": ["Turma", "Disciplina", "Professor", "Períodos fechados", "Quais"], "linhas": linhas}


# código -> (função, descrição, só coordenação?)
RELATORIOS = {
    "aulas": (r_aulas, "Aulas ministradas", False),
    "frequencia-turma": (r_frequencia_turma, "Frequência por turma", False),
    "frequencia-individual": (r_frequencia_individual, "Frequência individual", False),
    "baixa-frequencia": (r_baixa_frequencia, "Alunos com baixa frequência", False),
    "notas-medias": (r_notas_medias, "Notas e médias", False),
    "recuperacao": (r_recuperacao, "Alunos em recuperação", False),
    "conteudos": (r_conteudos, "Conteúdos ministrados", False),
    "desempenho-professores": (r_desempenho_professores, "Desempenho dos professores", True),
    "pendencias": (r_pendencias, "Pendências dos diários", True),
    "fechamento": (r_fechamento, "Fechamento por período letivo", True),
}


def boletim(matricula):
    """Boletim do aluno: todas as disciplinas da turma. Devolve (periodos, linhas)."""
    linhas, periodos, criterio = [], [], None
    for a in AlocacaoProfessor.objects.filter(instituicao=matricula.instituicao, turma=matricula.turma).select_related("disciplina", "ano_letivo"):
        ps, ls = calculos.desempenho_da_alocacao(a)
        periodos = periodos or ps
        criterio = criterio or servicos.criterios_da_alocacao(a)
        l = next((x for x in ls if x["matricula"].pk == matricula.pk), None)
        if l:
            linhas.append([a.disciplina.nome] + [_fmt(m) for m in l["medias"]] +
                          [_fmt(l["media_final"]), _fmt(l["frequencia"], "%"), l["situacao"]])
    cols = ["Disciplina"] + [p.nome for p in periodos] + ["Média final", "Frequência", "Situação"]
    return {"titulo": f"Boletim — {matricula.aluno.nome}", "colunas": cols, "linhas": linhas,
            "rodape": (f"Nota mínima: {_fmt(criterio[0])} · Frequência mínima: {_fmt(criterio[1], '%')}" if criterio else "")}
