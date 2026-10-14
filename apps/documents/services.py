"""Emissão de documentos: valida vínculos, monta o HTML final e grava o instantâneo + auditoria."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import escape, format_html

from apps.audit.services import registrar

from .models import CabecalhoDocumento, Destinatario, DocumentoEmitido, ModeloDocumento
from .variaveis import EXEMPLOS, montar_contexto, renderizar, sanitizar_html


def cabecalho_html(instituicao):
    """HTML do cabeçalho padrão (vazio se desativado). Valores do usuário são escapados."""
    cab = CabecalhoDocumento.objects.filter(instituicao=instituicao).first()
    if not cab or not cab.ativo:
        return "", ""
    partes = ['<div class="doc-cabecalho">']
    if cab.exibir_logotipo and cab.logotipo:
        partes.append(f'<img class="doc-logo" src="{escape(cab.logotipo.url)}" alt="Logotipo">')
    texto = [f'<div class="doc-cab-titulo">{escape(cab.titulo or instituicao.nome)}</div>']
    texto += [f'<div class="doc-cab-linha">{escape(l.strip())}</div>' for l in cab.linhas.splitlines() if l.strip()]
    partes.append(f'<div class="doc-cab-texto">{"".join(texto)}</div></div>')
    rodape = f'<div class="doc-rodape">{escape(cab.rodape)}</div>' if cab.rodape else ""
    return "".join(partes), rodape


def montar_html(modelo, valores, instituicao):
    corpo = renderizar(sanitizar_html(modelo.conteudo), valores)
    cab, rodape = cabecalho_html(instituicao) if modelo.usar_cabecalho else ("", "")
    return f'{cab}<div class="doc-corpo">{corpo}</div>{rodape}'


def previa_html(*, conteudo, usar_cabecalho, instituicao):
    """Pré-visualização do editor com valores de exemplo (nunca dados reais)."""
    modelo = ModeloDocumento(conteudo=conteudo, usar_cabecalho=usar_cabecalho)
    return montar_html(modelo, EXEMPLOS, instituicao)


@transaction.atomic
def emitir_documento(*, modelo, usuario, aluno=None, responsavel=None):
    inst = modelo.instituicao
    if not modelo.ativo:
        raise ValidationError("O modelo está inativo e não pode gerar documentos.")
    if modelo.destinatario == Destinatario.ALUNO and aluno is None:
        raise ValidationError("Este documento é emitido para um aluno.")
    if modelo.destinatario == Destinatario.RESPONSAVEL:
        if responsavel is None:
            raise ValidationError("Este documento é emitido para um responsável.")
        if aluno is not None and not aluno.responsaveis_financeiros.filter(responsavel=responsavel).exists():
            raise ValidationError("O aluno informado não está vinculado a este responsável.")
    if modelo.destinatario == Destinatario.ALUNO:
        responsavel = None  # o responsável vigente é obtido automaticamente
    for obj in (aluno, responsavel):
        if obj is not None and obj.instituicao_id != inst.pk:
            raise ValidationError("Registro de outra instituição.")

    valores = montar_contexto(instituicao=inst, usuario=usuario, aluno=aluno, responsavel=responsavel)
    doc = DocumentoEmitido.objects.create(
        instituicao=inst, modelo=modelo, tipo=modelo.tipo, titulo=modelo.nome, destinatario=modelo.destinatario,
        aluno=aluno, responsavel=responsavel if modelo.destinatario == Destinatario.RESPONSAVEL else None,
        html=montar_html(modelo, valores, inst), emitido_por=usuario)
    registrar(instituicao=inst, usuario=usuario, acao="DOCUMENTO_EMITIDO", entidade="DocumentoEmitido",
              entidade_id=doc.pk, aluno_id=aluno.pk if aluno else None,
              descricao=f"Documento emitido: {modelo.nome}",
              dados={"modelo_id": modelo.pk, "tipo": modelo.tipo, "destinatario": modelo.destinatario,
                     "responsavel_id": doc.responsavel_id})
    return doc
