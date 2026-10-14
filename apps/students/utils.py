import re


def so_digitos(valor):
    return re.sub(r"\D", "", valor or "")


def mascarar_documento(valor):
    """Oculta o miolo de CPF/CNPJ em listagens."""
    d = so_digitos(valor)
    if not d:
        return ""
    if len(d) == 11:
        return f"***.{d[3:6]}.***-{d[9:]}"
    if len(d) == 14:
        return f"**.***.{d[5:8]}/****-{d[12:]}"
    return "***"


def cpf_valido(valor):
    d = so_digitos(valor)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(d[j]) * (i + 1 - j) for j in range(i))
        dig = (soma * 10 % 11) % 10
        if dig != int(d[i]):
            return False
    return True


def cnpj_valido(valor):
    d = so_digitos(valor)
    if len(d) != 14 or d == d[0] * 14:
        return False
    def dv(base):
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):] if len(base) == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        r = sum(int(n) * p for n, p in zip(base, pesos)) % 11
        return 0 if r < 2 else 11 - r
    return dv(d[:12]) == int(d[12]) and dv(d[:13]) == int(d[13])


def formatar_cpf(valor):
    d = so_digitos(valor)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else valor


def formatar_cnpj(valor):
    d = so_digitos(valor)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else valor


def formatar_documento(valor):
    d = so_digitos(valor)
    if len(d) == 11:
        return formatar_cpf(d)
    if len(d) == 14:
        return formatar_cnpj(d)
    return valor
