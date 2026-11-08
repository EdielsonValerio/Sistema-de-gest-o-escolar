"""Avaliador SEGURO de fórmulas de nota (sem eval/exec): analisa a expressão com `ast` e só aceita números, variáveis
declaradas, operadores aritméticos/comparação/lógicos e um conjunto fechado de funções.

Exemplos: ``ATV + PROVA`` · ``(B1 + B2 + B3 + B4) / 4`` · ``SE(MF >= 6, MF, (MF + RF) / 2)`` · ``MAX(PROVA, RECUP)``
Funções: MAX, MIN, MEDIA, SOMA, ABS, ARRED(x, casas), SE(condição, então, senão). Decimal: use ponto (7.5).
Operadores: + - * / % **  < <= > >= == !=  and  or  not.
"""
import ast
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

LIMITE_TEXTO = 300
LIMITE_NOS = 80
FUNCOES = {"MAX", "MIN", "MEDIA", "SOMA", "ABS", "ARRED", "SE"}
RESERVADAS = FUNCOES | {"MF", "RF", "MA", "MP", "N", "MAXIMA", "AND", "OR", "NOT", "TRUE", "FALSE", "NONE"}
SIGLA_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,11}$")
FASE_VAR_RE = re.compile(r"^B\d+$")


class FormulaErro(ValueError):
    """Fórmula inválida (mensagem em português, própria para mostrar ao administrador)."""


def sigla_valida(sigla):
    return bool(SIGLA_RE.match(sigla or "")) and sigla not in RESERVADAS and not FASE_VAR_RE.match(sigla)


def _normalizar(texto):
    t = (texto or "").strip()
    if not t:
        raise FormulaErro("A fórmula está vazia.")
    if len(t) > LIMITE_TEXTO:
        raise FormulaErro(f"A fórmula é longa demais (máximo {LIMITE_TEXTO} caracteres).")
    t = t.upper()
    return re.sub(r"\b(AND|OR|NOT)\b", lambda m: m.group(1).lower(), t)


def _arvore(texto):
    try:
        arv = ast.parse(_normalizar(texto), mode="eval")
    except SyntaxError:
        raise FormulaErro("Fórmula com erro de sintaxe. Confira parênteses, operadores e vírgulas.")
    if sum(1 for _ in ast.walk(arv)) > LIMITE_NOS:
        raise FormulaErro("A fórmula é complexa demais.")
    return arv.body


def _num(v):
    if isinstance(v, bool):
        return Decimal(int(v))
    return v


def _avaliar(no, vars_):
    if isinstance(no, ast.Constant):
        if isinstance(no.value, bool) or not isinstance(no.value, (int, float)):
            raise FormulaErro("Só números são permitidos nas fórmulas.")
        return Decimal(str(no.value))
    if isinstance(no, ast.Name):
        nome = no.id.upper()
        if nome not in vars_:
            raise FormulaErro(f"Variável desconhecida: {nome}. Disponíveis: {', '.join(sorted(vars_))}.")
        return vars_[nome]
    if isinstance(no, ast.UnaryOp):
        v = _avaliar(no.operand, vars_)
        if isinstance(no.op, ast.USub):
            return -_num(v)
        if isinstance(no.op, ast.UAdd):
            return _num(v)
        if isinstance(no.op, ast.Not):
            return not bool(v)
        raise FormulaErro("Operador não permitido.")
    if isinstance(no, ast.BinOp):
        a, b = _num(_avaliar(no.left, vars_)), _num(_avaliar(no.right, vars_))
        try:
            if isinstance(no.op, ast.Add):
                return a + b
            if isinstance(no.op, ast.Sub):
                return a - b
            if isinstance(no.op, ast.Mult):
                return a * b
            if isinstance(no.op, ast.Div):
                return a / b
            if isinstance(no.op, ast.Mod):
                return a % b
            if isinstance(no.op, ast.Pow):
                if b != b.to_integral_value() or abs(b) > 4 or abs(a) > 1000:
                    raise FormulaErro("Potência permitida apenas com expoente inteiro de -4 a 4.")
                return a ** int(b)
        except (ZeroDivisionError, InvalidOperation):
            raise FormulaErro("Divisão por zero na fórmula.")
        raise FormulaErro("Operador não permitido.")
    if isinstance(no, ast.Compare):
        esq = _num(_avaliar(no.left, vars_))
        for op, dir_ in zip(no.ops, no.comparators):
            d = _num(_avaliar(dir_, vars_))
            ok = {ast.Lt: esq < d, ast.LtE: esq <= d, ast.Gt: esq > d, ast.GtE: esq >= d, ast.Eq: esq == d,
                  ast.NotEq: esq != d}.get(type(op))
            if ok is None:
                raise FormulaErro("Comparação não permitida.")
            if not ok:
                return False
            esq = d
        return True
    if isinstance(no, ast.BoolOp):
        vals = [bool(_avaliar(v, vars_)) for v in no.values]
        return all(vals) if isinstance(no.op, ast.And) else any(vals)
    if isinstance(no, ast.Call):
        if not isinstance(no.func, ast.Name) or no.keywords or no.func.id.upper() not in FUNCOES:
            raise FormulaErro(f"Função não permitida. Use: {', '.join(sorted(FUNCOES))}.")
        f = no.func.id.upper()
        args = [_avaliar(a, vars_) for a in no.args]
        if f == "SE":
            if len(args) != 3:
                raise FormulaErro("SE precisa de 3 argumentos: SE(condição, então, senão).")
            return args[1] if bool(args[0]) else args[2]
        args = [_num(a) for a in args]
        if not args:
            raise FormulaErro(f"{f} precisa de argumentos.")
        if f == "MAX":
            return max(args)
        if f == "MIN":
            return min(args)
        if f == "SOMA":
            return sum(args, Decimal(0))
        if f == "MEDIA":
            return sum(args, Decimal(0)) / len(args)
        if f == "ABS":
            if len(args) != 1:
                raise FormulaErro("ABS recebe 1 argumento.")
            return abs(args[0])
        if f == "ARRED":
            casas = int(args[1]) if len(args) > 1 else 0
            if len(args) > 2 or not 0 <= casas <= 3:
                raise FormulaErro("ARRED(x, casas) aceita de 0 a 3 casas.")
            return args[0].quantize(Decimal(1).scaleb(-casas), rounding=ROUND_HALF_UP)
    if isinstance(no, ast.Tuple):
        raise FormulaErro("Use ponto como separador decimal (7.5); a vírgula só separa argumentos de funções.")
    raise FormulaErro("Expressão não permitida na fórmula.")


def avaliar(texto, variaveis):
    """Calcula a fórmula com `variaveis` {NOME: Decimal}. Levanta FormulaErro se inválida."""
    vars_ = {k.upper(): (v if v is not None else Decimal(0)) for k, v in variaveis.items()}
    r = _avaliar(_arvore(texto), vars_)
    if isinstance(r, bool):
        raise FormulaErro("A fórmula deve resultar em um número (não em Verdadeiro/Falso).")
    try:
        return Decimal(r)
    except (InvalidOperation, TypeError):
        raise FormulaErro("A fórmula não resultou em um número.")


def validar(texto, nomes):
    """Valida a fórmula contra os nomes de variáveis permitidos. Devolve None se ok, ou a mensagem de erro."""
    nomes = [n.upper() for n in nomes]
    try:
        _arvore(texto)
        for base in (1, 2, 5):  # valores distintos evitam falso erro de divisão por zero
            avaliar(texto, {n: Decimal(base + i) for i, n in enumerate(nomes)})
    except FormulaErro as exc:
        return str(exc)
    return None
