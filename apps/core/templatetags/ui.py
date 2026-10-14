from decimal import Decimal, InvalidOperation

from django import template
from django.utils.html import format_html

register = template.Library()


@register.filter
def brl(valor):
    """Formata Decimal como moeda brasileira, sem usar float."""
    if valor in (None, ""):
        return "—"
    try:
        d = Decimal(valor).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    texto = f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Querystring atual com parâmetros substituídos (mantém filtros ao paginar/ordenar)."""
    params = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ""):
            params.pop(k, None)
        else:
            params[k] = v
    return params.urlencode()


@register.simple_tag
def badge_parcela(parcela):
    p = parcela
    if p.vencida:
        cor, txt = "danger", "Vencido"
    else:
        cor = {"ABERTA": "primary", "PARCIAL": "warning", "QUITADA": "success", "CANCELADA": "secondary"}[p.status]
        txt = p.get_status_display()
    return format_html('<span class="badge text-bg-{}">{}</span>', cor, txt)


@register.simple_tag
def badge_matricula(m):
    cor = {"PENDENTE": "warning", "ATIVA": "success", "TRANSFERIDA": "info", "CANCELADA": "secondary",
           "CONCLUIDA": "primary"}[m.status]
    return format_html('<span class="badge text-bg-{}">{}</span>', cor, m.get_status_display())


@register.filter
def field_class(bound_field):
    return "is-invalid" if bound_field.errors else ""
