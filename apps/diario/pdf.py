"""Exportação em PDF dos relatórios (reportlab)."""
from io import BytesIO
from xml.sax.saxutils import escape  # nosec B406 (só escapa texto; não faz parsing de XML)

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def gerar_pdf(rel, escola_nome):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm,
                            bottomMargin=1.5 * cm, title=rel["titulo"])
    st = getSampleStyleSheet()
    pequeno = st["BodyText"].clone("p", fontSize=8, leading=10)
    cab = st["BodyText"].clone("c", fontSize=8, leading=10, textColor=colors.white, fontName="Helvetica-Bold")
    el = [Paragraph(escape(escola_nome), st["Heading3"]), Paragraph(escape(rel["titulo"]), st["Heading2"]),
          Paragraph(f"Emitido em {timezone.localtime():%d/%m/%Y %H:%M}", pequeno), Spacer(1, 8)]
    dados = [[Paragraph(escape(str(c)), cab) for c in rel["colunas"]]]
    for linha in rel["linhas"]:
        dados.append([Paragraph(escape(str(c)), pequeno) for c in linha])
    if len(dados) == 1:
        el.append(Paragraph("Nenhum registro encontrado.", pequeno))
    else:
        t = Table(dados, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")])]))
        el.append(t)
    if rel.get("rodape"):
        el += [Spacer(1, 8), Paragraph(escape(rel["rodape"]), pequeno)]
    doc.build(el)
    return buf.getvalue()
