"""Ferramentas (somente leitura, agregadas) que o assistente pode usar. O modelo NUNCA acessa o banco.

Cada ferramenta recebe a escola **do usuário logado** (nunca vinda do modelo), só é oferecida se o usuário tem as
permissões do Django exigidas e devolve apenas números agregados — sem nome, CPF, endereço, telefone ou e-mail.
"""
from datetime import date
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.enrollments.models import Matricula
from apps.finance.models import Parcela, Recebimento
from apps.payables.models import ContaPagar

from .lgpd import agrupar_pequenos

MAX_MESES = 24
MAX_ROTULOS = 40
MAX_SERIES = 4
TIPOS_GRAFICO = {"bar", "line", "pie"}
ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=2))


def _f(valor):
    return round(float(valor or 0), 2)


def _meses(params, padrao=12):
    try:
        n = int(params.get("meses", padrao))
    except (TypeError, ValueError):
        n = padrao
    return max(1, min(n, MAX_MESES))


def _lista_meses(n, hoje=None):
    """Últimos n meses (primeiro dia de cada mês), do mais antigo ao atual."""
    hoje = hoje or timezone.localdate()
    ano, mes = hoje.year, hoje.month
    out = []
    for _ in range(n):
        out.append(date(ano, mes, 1))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return list(reversed(out))


def _prox_mes(d):
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def _rot_mes(d):
    return f"{d.month:02d}/{d.year}"


# --------------------------------------------------------------------------- ferramentas de dados
def matriculas_por_turma(escola, params):
    qs = (Matricula.objects.filter(instituicao=escola, status=Matricula.Status.ATIVA)
          .values("turma__nome").annotate(matriculas_ativas=Count("aluno", distinct=True)).order_by("turma__nome"))
    linhas = [{"turma": r["turma__nome"], "matriculas_ativas": r["matriculas_ativas"]} for r in qs]
    linhas, omitido = agrupar_pequenos(linhas, "turma", "matriculas_ativas")
    return {"linhas": linhas, "nota": "Turmas com menos de 3 alunos foram agrupadas em 'Outros' (LGPD)."
            + (" Havia grupos pequenos demais, omitidos." if omitido else "")}


def recebimentos_por_mes(escola, params):
    meses = _lista_meses(_meses(params))
    qs = (Recebimento.objects.filter(instituicao=escola, status=Recebimento.Status.CONFIRMADO,
                                     criado_em__date__gte=meses[0], criado_em__date__lt=_prox_mes(meses[-1]))
          .annotate(m=TruncMonth("criado_em")).values("m")
          .annotate(valor=Coalesce(Sum("valor_total"), ZERO), qtd=Count("id")))
    por_mes = {r["m"].date() if hasattr(r["m"], "date") else r["m"]: r for r in qs}
    return {"linhas": [{"mes": _rot_mes(m), "valor_recebido": _f(por_mes.get(m, {}).get("valor")),
                        "qtd_recebimentos": por_mes.get(m, {}).get("qtd", 0)} for m in meses]}


def _alunos_matriculados_no_mes(escola, mes):
    fim = _prox_mes(mes)
    return (Matricula.objects.filter(instituicao=escola, data_ingresso__lt=fim)
            .filter(status__in=[Matricula.Status.ATIVA, Matricula.Status.CONCLUIDA, Matricula.Status.TRANSFERIDA])
            .filter(Q(data_saida__isnull=True) | Q(data_saida__gte=mes))
            .values("aluno").distinct().count())


def evolucao_matriculas_e_recebimentos(escola, params):
    meses = _lista_meses(_meses(params))
    rec = {l["mes"]: l for l in recebimentos_por_mes(escola, params)["linhas"]}
    linhas = [{"mes": _rot_mes(m), "alunos_matriculados": _alunos_matriculados_no_mes(escola, m),
               "valor_recebido": rec[_rot_mes(m)]["valor_recebido"]} for m in meses]
    return {"linhas": linhas}


def inadimplencia_por_turma(escola, params):
    hoje = timezone.localdate()
    qs = (Parcela.objects.filter(instituicao=escola, vencimento__lt=hoje,
                                 status__in=[Parcela.Status.ABERTA, Parcela.Status.PARCIAL])
          .values("matricula__turma__nome")
          .annotate(alunos_inadimplentes=Count("aluno", distinct=True),
                    valor_vencido=Coalesce(Sum(F("valor_devido") - F("valor_pago")), ZERO))
          .order_by("matricula__turma__nome"))
    linhas = [{"turma": r["matricula__turma__nome"], "alunos_inadimplentes": r["alunos_inadimplentes"],
               "valor_vencido": _f(r["valor_vencido"])} for r in qs]
    linhas, omitido = agrupar_pequenos(linhas, "turma", "alunos_inadimplentes", somas=("valor_vencido",))
    return {"linhas": linhas, "nota": "Grupos com menos de 3 alunos foram agrupados em 'Outros' (LGPD)."
            + (" Havia grupos pequenos demais, omitidos." if omitido else "")}


def a_receber_por_mes(escola, params):
    n = _meses(params, 6)
    hoje = timezone.localdate()
    inicio = date(hoje.year, hoje.month, 1)
    meses = [inicio]
    while len(meses) < n:
        meses.append(_prox_mes(meses[-1]))
    qs = (Parcela.objects.filter(instituicao=escola, vencimento__gte=inicio, vencimento__lt=_prox_mes(meses[-1]))
          .exclude(status=Parcela.Status.CANCELADA)
          .annotate(m=TruncMonth("vencimento")).values("m")
          .annotate(previsto=Coalesce(Sum("valor_devido"), ZERO), pago=Coalesce(Sum("valor_pago"), ZERO)))
    por = {(r["m"].date() if hasattr(r["m"], "date") else r["m"]): r for r in qs}
    return {"linhas": [{"mes": _rot_mes(m), "valor_previsto": _f(por.get(m, {}).get("previsto")),
                        "valor_ja_pago": _f(por.get(m, {}).get("pago"))} for m in meses]}


def contas_a_pagar_resumo(escola, params):
    hoje = timezone.localdate()
    abertas = ContaPagar.objects.filter(instituicao=escola, status=ContaPagar.Status.ABERTA)
    por_cat = (abertas.values("categoria").annotate(valor=Coalesce(Sum("valor"), ZERO), qtd=Count("id"))
               .order_by("categoria"))
    return {"linhas": [{"categoria": r["categoria"], "valor_em_aberto": _f(r["valor"]), "contas": r["qtd"]}
                       for r in por_cat],
            "total_em_aberto": _f(abertas.aggregate(t=Coalesce(Sum("valor"), ZERO))["t"]),
            "total_vencido": _f(abertas.filter(vencimento__lt=hoje).aggregate(t=Coalesce(Sum("valor"), ZERO))["t"])}


_MESES_PARAM = {"type": "object", "properties": {"meses": {
    "type": "integer", "minimum": 1, "maximum": MAX_MESES, "description": "Quantidade de meses (padrão 12)."}}}
_SEM_PARAM = {"type": "object", "properties": {}}

FERRAMENTAS = {
    "matriculas_por_turma": {
        "descricao": "Número de matrículas ativas por turma (grupos com menos de 3 alunos aparecem como 'Outros').",
        "schema": _SEM_PARAM, "perms": ["enrollments.view_matricula"], "func": matriculas_por_turma},
    "recebimentos_por_mes": {
        "descricao": "Valor recebido (R$) e quantidade de recebimentos por mês nos últimos N meses.",
        "schema": _MESES_PARAM, "perms": ["finance.ver_relatorios"], "func": recebimentos_por_mes},
    "evolucao_matriculas_e_recebimentos": {
        "descricao": "Por mês: alunos matriculados e valor recebido (R$). Ideal para comparar matrículas × recebimentos.",
        "schema": _MESES_PARAM, "perms": ["enrollments.view_matricula", "finance.ver_relatorios"],
        "func": evolucao_matriculas_e_recebimentos},
    "inadimplencia_por_turma": {
        "descricao": "Por turma: alunos com parcelas vencidas e valor vencido em aberto (R$). Sem nomes.",
        "schema": _SEM_PARAM, "perms": ["finance.view_parcela"], "func": inadimplencia_por_turma},
    "a_receber_por_mes": {
        "descricao": "Valor previsto e já pago das parcelas que vencem nos próximos N meses (padrão 6).",
        "schema": _MESES_PARAM, "perms": ["finance.view_parcela"], "func": a_receber_por_mes},
    "contas_a_pagar_resumo": {
        "descricao": "Contas a pagar em aberto por categoria, total em aberto e total vencido (R$). Sem fornecedores.",
        "schema": _SEM_PARAM, "perms": ["payables.view_contapagar"], "func": contas_a_pagar_resumo},
}

SCHEMA_GRAFICO = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string", "maxLength": 120},
        "rotulos": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_ROTULOS,
                    "description": "Rótulos do eixo X (ou fatias na pizza)."},
        "series": {"type": "array", "maxItems": MAX_SERIES, "items": {"type": "object", "properties": {
            "nome": {"type": "string"},
            "valores": {"type": "array", "items": {"type": "number"}},
            "tipo": {"type": "string", "enum": ["bar", "line", "pie"]},
            "eixo": {"type": "string", "enum": ["esquerda", "direita"]},
            "unidade": {"type": "string", "description": "'R$' para dinheiro; vazio para contagens."}},
            "required": ["nome", "valores"]}}},
    "required": ["titulo", "rotulos", "series"],
}


def disponiveis(user):
    """Ferramentas que o usuário pode usar, conforme as permissões do Django."""
    return {n: f for n, f in FERRAMENTAS.items() if all(user.has_perm(p) for p in f["perms"])}


def definicoes(user):
    """Definições no formato da API do Claude (inclui `emitir_grafico` se houver ao menos uma ferramenta de dados)."""
    dispo = disponiveis(user)
    defs = [{"name": n, "description": f["descricao"], "input_schema": f["schema"]} for n, f in dispo.items()]
    if defs:
        defs.append({"name": "emitir_grafico", "description": (
            "Exibe um gráfico ao usuário. Use SOMENTE números devolvidos pelas outras ferramentas. "
            "Para comparar grandezas diferentes (contagem × R$) use 'eixo' esquerda/direita."),
            "input_schema": SCHEMA_GRAFICO})
    return defs


def validar_grafico(dados):
    """Valida e normaliza o gráfico pedido pelo modelo. Levanta ValueError com mensagem clara."""
    if not isinstance(dados, dict):
        raise ValueError("gráfico inválido")
    rotulos = dados.get("rotulos")
    series = dados.get("series")
    if not isinstance(rotulos, list) or not rotulos or len(rotulos) > MAX_ROTULOS:
        raise ValueError(f"informe de 1 a {MAX_ROTULOS} rótulos")
    if not isinstance(series, list) or not series or len(series) > MAX_SERIES:
        raise ValueError(f"informe de 1 a {MAX_SERIES} séries")
    out_series = []
    for s in series:
        if not isinstance(s, dict) or not isinstance(s.get("valores"), list) or len(s["valores"]) != len(rotulos):
            raise ValueError("cada série precisa ter um valor por rótulo")
        valores = []
        for v in s["valores"]:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("os valores do gráfico devem ser numéricos")
            valores.append(round(float(v), 2))
        tipo = s.get("tipo") if s.get("tipo") in TIPOS_GRAFICO else "bar"
        out_series.append({"nome": str(s.get("nome", ""))[:60], "valores": valores, "tipo": tipo,
                           "eixo": "direita" if s.get("eixo") == "direita" else "esquerda",
                           "unidade": "R$" if s.get("unidade") == "R$" else ""})
    if any(s["tipo"] == "pie" for s in out_series) and len(out_series) > 1:
        out_series = out_series[:1]
    return {"titulo": str(dados.get("titulo", "Gráfico"))[:120], "rotulos": [str(r)[:40] for r in rotulos],
            "series": out_series}
