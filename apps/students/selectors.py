"""Consultas reutilizáveis de alunos (tela de consulta e caixa)."""
from django.db.models import Q

from .utils import formatar_cpf, so_digitos


def filtrar_por_busca(qs, q):
    """Código, nome, CPF, número de matrícula, nome da mãe ou do responsável financeiro."""
    q = (q or "").strip()
    if not q:
        return qs
    digitos = so_digitos(q)
    cond = (Q(codigo__icontains=q) | Q(nome__icontains=q) | Q(nome_social__icontains=q)
            | Q(nome_mae__icontains=q) | Q(matriculas__numero__icontains=q)
            | Q(responsaveis_financeiros__responsavel__nome__icontains=q))
    if len(digitos) == 11:
        cond |= Q(cpf=formatar_cpf(digitos))
    elif q.replace(".", "").replace("-", "").isdigit() and len(digitos) >= 3:
        cond |= Q(cpf__icontains=q)
    return qs.filter(cond).distinct()
