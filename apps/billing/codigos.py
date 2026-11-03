"""Códigos de boleto e Pix: linha digitável ⇄ código de barras, desenho do código de barras (ITF) e BR Code.

Usado para o boleto simulado e para desenhar/validar o que o banco devolve.
"""
import base64
import io
from datetime import date

from django.utils.safestring import mark_safe

# --------------------------------------------------------------------------- dígitos verificadores


def _mod10(digitos):
    soma, peso = 0, 2
    for d in reversed(digitos):
        p = int(d) * peso
        soma += p // 10 + p % 10
        peso = 1 if peso == 2 else 2
    return (10 - soma % 10) % 10


def _mod11_barras(digitos):
    soma, peso = 0, 2
    for d in reversed(digitos):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    dv = 11 - soma % 11
    return 1 if dv in (0, 10, 11) else dv


def fator_vencimento(vencimento):
    """Fator de vencimento do boleto (ciclo reiniciado em 22/02/2025 = 1000)."""
    return 1000 + (vencimento - date(2025, 2, 22)).days


def montar_barras(*, banco, valor_centavos, vencimento, campo_livre):
    """Código de barras de 44 dígitos (banco, moeda 9, DV, fator, valor e campo livre de 25 dígitos)."""
    assert len(campo_livre) == 25 and len(banco) == 3
    sem_dv = f"{banco}9{fator_vencimento(vencimento):04d}{valor_centavos:010d}{campo_livre}"
    return f"{sem_dv[:4]}{_mod11_barras(sem_dv)}{sem_dv[4:]}"


def barras_para_linha(barras):
    """Código de barras (44) → linha digitável (47)."""
    c1, c2, c3 = barras[0:4] + barras[19:24], barras[24:34], barras[34:44]
    return f"{c1}{_mod10(c1)}{c2}{_mod10(c2)}{c3}{_mod10(c3)}{barras[4]}{barras[5:19]}"


def linha_para_barras(linha):
    """Linha digitável (47 dígitos, com ou sem pontuação) → código de barras (44). `''` se não for válida."""
    d = "".join(ch for ch in str(linha) if ch.isdigit())
    if len(d) != 47:
        return ""
    return d[0:4] + d[32] + d[33:47] + d[4:9] + d[10:20] + d[21:31]


def formatar_linha(linha):
    d = "".join(ch for ch in str(linha) if ch.isdigit())
    if len(d) != 47:
        return linha
    return f"{d[0:5]}.{d[5:10]} {d[10:15]}.{d[15:21]} {d[21:26]}.{d[26:32]} {d[32]} {d[33:]}"


# --------------------------------------------------------------------------- código de barras (ITF)

_ITF = ["00110", "10001", "01001", "11000", "00101", "10100", "01100", "00011", "10010", "01010"]


def barras_svg(codigo, altura=50):
    """SVG do código de barras Intercalado 2 de 5 (padrão dos boletos). Devolve '' se o código for inválido."""
    codigo = "".join(ch for ch in str(codigo) if ch.isdigit())
    if not codigo or len(codigo) % 2:
        return ""
    elementos = [(1, True), (1, False), (1, True), (1, False)]  # início
    for i in range(0, len(codigo), 2):
        a, b = _ITF[int(codigo[i])], _ITF[int(codigo[i + 1])]
        for j in range(5):
            elementos.append((3 if a[j] == "1" else 1, True))
            elementos.append((3 if b[j] == "1" else 1, False))
    elementos += [(3, True), (1, False), (1, True)]  # fim
    x, retangulos = 0, []
    for largura, barra in elementos:
        if barra:
            retangulos.append(f'<rect x="{x}" y="0" width="{largura}" height="{altura}"/>')
        x += largura
    return mark_safe(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {x} {altura}" width="100%" '
                     f'height="{altura}" preserveAspectRatio="none" role="img" aria-label="Código de barras" '
                     f'fill="#000">{"".join(retangulos)}</svg>')


# --------------------------------------------------------------------------- Pix (BR Code)

def crc16(texto):
    """CRC16/CCITT-FALSE usado no BR Code."""
    crc = 0xFFFF
    for byte in texto.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


def _campo(id_, valor):
    return f"{id_}{len(valor):02d}{valor}"


def brcode(*, chave, valor, nome, cidade, txid="***"):
    """Pix 'copia e cola' estático (EMV). Para o modo simulado; o banco real devolve o seu próprio código."""
    conta = _campo("00", "br.gov.bcb.pix") + _campo("01", chave)
    corpo = (_campo("00", "01") + _campo("26", conta) + _campo("52", "0000") + _campo("53", "986")
             + _campo("54", f"{valor:.2f}") + _campo("58", "BR") + _campo("59", nome[:25] or "NA")
             + _campo("60", cidade[:15] or "NA") + _campo("62", _campo("05", txid[:25] or "***")) + "6304")
    return corpo + crc16(corpo)


def qrcode_base64(texto):
    """PNG (base64) do QR Code do texto informado."""
    import qrcode

    img = qrcode.make(texto, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
