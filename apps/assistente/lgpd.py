"""Proteção de dados (LGPD) do assistente de IA.

Regras: só números agregados saem do sistema; grupos com menos de `MIN_GRUPO` pessoas são agrupados em "Outros";
dados pessoais digitados pelo usuário na pergunta (CPF, CNPJ, e-mail, telefone) são removidos antes do envio.
"""
import re

MIN_GRUPO = 3
OUTROS = "Outros (grupos pequenos)"

_PADROES = [
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ removido]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF removido]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[e-mail removido]"),
    (re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)"), "[telefone removido]"),
]


def redigir(texto):
    """Remove dados pessoais do texto. Devolve (texto_limpo, houve_remocao)."""
    novo = texto
    for padrao, subst in _PADROES:
        novo = padrao.sub(subst, novo)
    return novo, novo != texto


def agrupar_pequenos(linhas, rotulo, contagem, somas=()):
    """Junta em "Outros" as linhas cuja `contagem` (nº de pessoas) < MIN_GRUPO. Se o próprio "Outros" ainda ficar
    abaixo do mínimo, ele é omitido (devolve `omitido=True`) para não identificar ninguém."""
    grandes, pequenas = [], []
    for l in linhas:
        (grandes if l[contagem] >= MIN_GRUPO else pequenas).append(l)
    omitido = False
    if pequenas:
        total = sum(l[contagem] for l in pequenas)
        if total >= MIN_GRUPO:
            outro = {rotulo: OUTROS, contagem: total}
            for campo in somas:
                outro[campo] = round(sum(l[campo] for l in pequenas), 2)
            grandes.append(outro)
        else:
            omitido = True
    return grandes, omitido
