"""Módulos contratados por escola (venda por módulo).

* A **Secretaria** (alunos, responsáveis, turmas, matrículas, documentos, usuários, auditoria) é a base: sempre ativa.
* Os demais módulos são habilitados SOMENTE pelo administrador do sistema, na ficha da escola.
* Módulo desligado = menu escondido, telas bloqueadas e permissões retiradas; os dados são preservados e voltam
  quando o módulo é religado. O assistente de IA mantém o próprio interruptor (`ConfiguracaoIA.habilitada`).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Modulo:
    codigo: str
    nome: str
    descricao: str
    apps: tuple = ()           # apps Django cujas permissões e telas pertencem ao módulo
    exige: tuple = ()          # módulos que precisam estar ativos antes
    rotas: tuple = ()          # (app_name, url_name) avulsos que também pertencem ao módulo


MODULOS = {m.codigo: m for m in (
    Modulo("diario", "Diário de classe, coordenação, boletim e histórico",
           "Professores, alocações, avaliação, fechamento de fases, boletim e histórico escolar.", apps=("diario",)),
    Modulo("financeiro", "Financeiro",
           "Planos, contas a receber, caixa, relatórios e contas a pagar.", apps=("finance", "payables")),
    Modulo("cobranca", "Cobrança bancária (boletos e Pix)",
           "Emissão e baixa automática de boletos pelo banco.", apps=("billing",), exige=("financeiro",)),
    Modulo("portal", "Portal do responsável",
           "Consulta de dados, boletim, financeiro e documentos pelos responsáveis.", apps=("portal",)),
    Modulo("prematricula", "Pré-matrícula online",
           "Link público de pré-matrícula.", rotas=(("students", "pre_matricula_config"),)),  # as páginas públicas conferem por conta própria
    Modulo("comunicados", "Comunicados por WhatsApp",
           "Configuração da API do WhatsApp e envio de comunicados pela coordenação.", apps=("comunicacao",)),
)}
IA = "ia"  # interruptor próprio (apps.assistente)
TODOS = tuple(MODULOS)

# Rotas que continuam funcionando mesmo com o módulo desligado.
ROTAS_LIVRES = {("billing", "webhook")}  # o banco avisa pagamentos de boletos já emitidos: nunca perder baixa


def escolhas():
    return [(m.codigo, m.nome) for m in MODULOS.values()]


def normalizar(codigos):
    return [c for c in TODOS if c in set(codigos or [])]


def dependencias_faltando(codigos):
    """[(módulo, exigido)] das dependências não satisfeitas."""
    c = set(codigos or [])
    return [(m, e) for m in c if m in MODULOS for e in MODULOS[m].exige if e not in c]


def modulo_da_rota(app_name, url_name):
    if (app_name, url_name) in ROTAS_LIVRES:
        return None
    for m in MODULOS.values():
        if app_name in m.apps or (app_name, url_name) in m.rotas:
            if app_name == "portal" and url_name not in ("config", "acesso"):
                continue  # o portal público decide por conta própria (não há escola na requisição)
            return m.codigo
    return None


def modulo_da_permissao(app_label, codename):
    for m in MODULOS.values():
        if app_label in m.apps:
            return m.codigo
    if app_label == "students" and "linkprematricula" in codename:
        return "prematricula"
    return None


def tem_modulo(escola, codigo):
    if escola is None:
        return False
    if codigo == IA:
        from apps.assistente.models import ConfiguracaoIA
        return ConfiguracaoIA.objects.filter(instituicao=escola, habilitada=True).exists()
    return codigo in (escola.modulos or [])


def permissao_bloqueada(escola, perm):
    """`perm` no formato 'app.codename'. True se pertence a um módulo que a escola não contratou."""
    try:
        app_label, codename = perm.split(".", 1)
    except ValueError:
        return False
    cod = modulo_da_permissao(app_label, codename)
    return cod is not None and cod not in (escola.modulos or [])
