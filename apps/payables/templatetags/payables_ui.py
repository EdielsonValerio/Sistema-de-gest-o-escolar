from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def badge_conta(conta):
    """Selo da situação da conta a pagar. Vencida = em aberto com data de pagamento anterior a hoje."""
    if conta.status == "LIQUIDADA":
        return format_html('<span class="badge text-bg-success">Liquidada</span>')
    if conta.status == "DESATIVADA":
        return format_html('<span class="badge text-bg-secondary">Desativada</span>')
    if conta.vencida:
        return format_html('<span class="badge text-bg-danger">Vencida</span>')
    d = conta.dias_para_vencer
    if d == 0:
        return format_html('<span class="badge text-bg-warning">Vence hoje</span>')
    return format_html('<span class="badge text-bg-primary">Em aberto</span>')


@register.filter
def classe_prazo(conta):
    d = conta.dias_para_vencer
    return "text-danger" if d < 0 else "text-warning-emphasis" if d <= 1 else "text-muted"
