"""Proteção de dados (LGPD) do assistente de IA.

Regras: só números agregados saem do sistema; grupos com menos de `MIN_GRUPO` pessoas são agrupados em "Outros";
dados pessoais digitados pelo usuário na pergunta (CPF, CNPJ, e-mail, telefone e nomes de pessoas cadastradas na escola) são removidos antes do envio.
"""
import re
import unicodedata

MIN_GRUPO = 3
OUTROS = "Outros (grupos pequenos)"

_PADROES = [
    (re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"), "[CNPJ removido]"),
    (re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"), "[CPF removido]"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[e-mail removido]"),
    (re.compile(r"(?<!\d)(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}(?!\d)"), "[telefone removido]"),
]


MAX_NOMES = 20000


def _dobrar(texto):
    """Minúsculas e sem acentos, mantendo o MESMO comprimento (posições do texto original continuam valendo)."""
    saida = []
    for c in unicodedata.normalize("NFC", texto):
        b = unicodedata.normalize("NFD", c)[0].lower()
        saida.append(b if len(b) == 1 else c)
    return "".join(saida)


def nomes_da_escola(escola):
    """Nomes de pessoas cadastradas na escola (alunos, responsáveis, pais, usuários) para mascarar no texto livre.

    Só a escola da pergunta é consultada. Nomes de uma palavra só não entram (confundiriam palavras comuns)."""
    from django.contrib.auth.models import User

    from apps.students.models import Aluno, Responsavel

    brutos = set()
    for campos in (("nome", "nome_social", "nome_mae", "nome_pai", "nome_responsavel_legal"),):
        for linha in Aluno.objects.filter(instituicao=escola).values_list(*campos)[:MAX_NOMES]:
            brutos.update(linha)
    brutos.update(Responsavel.objects.filter(instituicao=escola).values_list("nome", flat=True)[:MAX_NOMES])
    for fn, ln in User.objects.filter(perfil__instituicao=escola).values_list("first_name", "last_name")[:MAX_NOMES]:
        brutos.add(f"{fn} {ln}")
    return {" ".join(n.split()) for n in brutos if n and len(n.split()) >= 2}


def _regex_nomes(nomes):
    """Casa o nome completo e também os dois primeiros nomes (ex.: "Ana Beatriz" de "Ana Beatriz Souza")."""
    variantes = set()
    for n in nomes:
        partes = _dobrar(n).split()
        variantes.add(" ".join(partes))
        if len(partes) > 2:
            variantes.add(" ".join(partes[:2]))
    if not variantes:
        return None
    alt = "|".join(re.escape(v).replace(r"\ ", r"\s+") for v in sorted(variantes, key=len, reverse=True))
    return re.compile(rf"(?<!\w)(?:{alt})(?!\w)")


def mascarar_nomes(texto, nomes):
    texto = unicodedata.normalize("NFC", texto)
    rx = _regex_nomes(nomes or ())
    if rx is None:
        return texto
    dobrado, partes, pos = _dobrar(texto), [], 0
    for m in rx.finditer(dobrado):
        partes += [texto[pos:m.start()], "[nome removido]"]
        pos = m.end()
    return "".join(partes) + texto[pos:]


def redigir(texto, nomes=()):
    """Remove dados pessoais do texto (CPF, CNPJ, e-mail, telefone e nomes de pessoas da escola, se informados).
    Devolve (texto_limpo, houve_remocao)."""
    novo = texto
    for padrao, subst in _PADROES:
        novo = padrao.sub(subst, novo)
    novo = mascarar_nomes(novo, nomes)
    return novo, novo != unicodedata.normalize("NFC", texto)


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
