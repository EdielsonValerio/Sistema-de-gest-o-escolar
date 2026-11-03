"""Isolamento por escola (instituição) na camada de dados.

Toda tabela de negócio tem uma coluna `instituicao_id` NOT NULL. Além disso, antes de gravar qualquer registro,
conferimos que os registros aos quais ele se liga (aluno, turma, parcela, ...) pertencem à MESMA escola. Assim, um
erro de programação (ou uma requisição forjada) não consegue ligar dados de uma escola aos de outra.

Observação: `bulk_create`/`update` não disparam este gatilho; nesses pontos use `verificar_escola` explicitamente.
"""
from django.core.exceptions import SuspiciousOperation
from django.db.models.signals import pre_save

APPS_DE_NEGOCIO = {"core", "accounts", "students", "academic", "enrollments", "finance", "audit", "documents", "payables", "billing", "assistente"}
# Tabelas compartilhadas por todas as escolas (administradas somente pelo administrador do sistema).
GLOBAIS = {"academic.seriereferencia", "core.instituicao", "billing.titulosimulado"}


class EscolaIncompativel(SuspiciousOperation):
    """Tentativa de gravar dados que misturam escolas (resulta em HTTP 400 e é registrada pelo Django)."""


def _tem_campo(model, nome):
    return any(f.name == nome for f in model._meta.concrete_fields)


def eh_modelo_de_escola(model):
    meta = model._meta
    return meta.app_label in APPS_DE_NEGOCIO and meta.label_lower not in GLOBAIS and _tem_campo(model, "instituicao")


def verificar_escola(instance):
    """Herda a escola do registro pai (quando declarado) e valida a coerência com os demais vínculos."""
    model = type(instance)
    pai = getattr(model, "INSTITUICAO_DE", None)
    if instance.instituicao_id is None and pai:
        origem = getattr(instance, pai, None)
        if origem is not None:
            instance.instituicao_id = origem.instituicao_id
    if instance.instituicao_id is None:
        raise EscolaIncompativel(f"{model._meta.label}: registro sem escola.")

    for campo in model._meta.concrete_fields:
        if not (campo.many_to_one or campo.one_to_one) or campo.name == "instituicao":
            continue
        alvo = campo.related_model
        if not _tem_campo(alvo, "instituicao"):
            continue
        valor = getattr(instance, campo.attname)
        if valor is None:
            continue
        if campo.is_cached(instance):
            escola_alvo = campo.get_cached_value(instance).instituicao_id
        elif instance._state.adding:  # em atualizações só o que foi carregado/atribuído é conferido
            escola_alvo = alvo._base_manager.filter(pk=valor).values_list("instituicao_id", flat=True).first()
        else:
            continue
        if escola_alvo != instance.instituicao_id:
            raise EscolaIncompativel(
                f"{model._meta.label}.{campo.name} pertence a outra escola (dados de escolas diferentes não "
                "podem ser relacionados).")


def _antes_de_gravar(sender, instance, raw=False, **kwargs):
    if raw or not eh_modelo_de_escola(sender):
        return
    verificar_escola(instance)


def conectar():
    pre_save.connect(_antes_de_gravar, dispatch_uid="apps.core.tenant.antes_de_gravar")
