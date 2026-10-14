from django.db import IntegrityError, transaction

from apps.core.numbering import proxima_sequencia
from apps.core.models import TipoNumeracao

from .models import SerieReferencia


def gerar_codigo_turma(instituicao):
    """Código interno estável (T0001...). Usa contador próprio e transacional."""
    # Reaproveita SequenciaNumeracao com um tipo textual dedicado.
    with transaction.atomic():
        n = proxima_sequencia(instituicao, "TURMA_CODIGO", "GLOBAL")
    return f"T{n:05d}"


SERIES_PADRAO = [
    # (codigo interno, nome, etapa, ordem)
    ("EI-BERCARIO", "Berçário", "EI", 1), ("EI-MATERNAL", "Maternal", "EI", 2),
    ("EI-PRE1", "Pré I", "EI", 3), ("EI-PRE2", "Pré II", "EI", 4),
    ("EF1-01", "1º ano do Ensino Fundamental", "EF1", 1), ("EF1-02", "2º ano do Ensino Fundamental", "EF1", 2),
    ("EF1-03", "3º ano do Ensino Fundamental", "EF1", 3), ("EF1-04", "4º ano do Ensino Fundamental", "EF1", 4),
    ("EF1-05", "5º ano do Ensino Fundamental", "EF1", 5),
    ("EF2-06", "6º ano do Ensino Fundamental", "EF2", 6), ("EF2-07", "7º ano do Ensino Fundamental", "EF2", 7),
    ("EF2-08", "8º ano do Ensino Fundamental", "EF2", 8), ("EF2-09", "9º ano do Ensino Fundamental", "EF2", 9),
    ("EM-01", "1ª série do Ensino Médio", "EM", 1), ("EM-02", "2ª série do Ensino Médio", "EM", 2),
    ("EM-03", "3ª série do Ensino Médio", "EM", 3),
]


def carregar_series_padrao():
    """Cria as séries de referência que ainda não existem (idempotente). Códigos são internos."""
    criadas = 0
    for codigo, nome, etapa, ordem in SERIES_PADRAO:
        _, novo = SerieReferencia.objects.get_or_create(
            codigo=codigo, defaults={"nome": nome, "etapa": etapa, "ordem": ordem})
        criadas += novo
    return criadas
