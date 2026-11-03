from django import template
from django.utils.html import format_html

from ..codigos import barras_svg, formatar_linha, linha_para_barras

register = template.Library()

_CLASSES = {"PENDENTE": "text-bg-warning", "PAGA": "text-bg-success", "CANCELADA": "text-bg-secondary",
            "ERRO": "text-bg-danger"}


@register.filter
def badge_cobranca(c):
    return format_html('<span class="badge {}">{}</span>', _CLASSES.get(c.status, "text-bg-light"), c.get_status_display())


@register.filter
def linha_fmt(linha):
    return formatar_linha(linha)


@register.filter
def barras_do_boleto(cobranca):
    """SVG do código de barras: usa o código do banco ou o deriva da linha digitável."""
    codigo = cobranca.codigo_barras or linha_para_barras(cobranca.linha_digitavel)
    return barras_svg(codigo)
