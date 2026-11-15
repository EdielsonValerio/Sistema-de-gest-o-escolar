"""Emissão do boletim e do histórico escolar (somente pelos botões da ficha do aluno)."""
from django.core.exceptions import ValidationError

from apps.documents.models import Destinatario, ModeloDocumento
from apps.documents.services import emitir_documento

from . import boletim as b

PADRAO_BOLETIM = """<h2 style="text-align: center">BOLETIM ESCOLAR [anoLetivoBoletim]</h2>
<p><strong>Aluno:</strong> [nomeAluno] — <strong>Código:</strong> [codigoUnicoAluno]</p>
<p><strong>Série:</strong> [serieBoletim] — <strong>Turma:</strong> [turmaBoletim] — <strong>Matrícula:</strong> [numeroMatricula]</p>
<div>[tabelaBoletim]</div>
<p><strong>Resultado final:</strong> [resultadoFinal] — <strong>Média geral:</strong> [mediaGeral] — <strong>Frequência geral:</strong> [frequenciaGeral]</p>
<p>Fases concluídas: [fasesLiberadas]. As notas de cada fase são lançadas ao boletim quando o professor conclui a fase.</p>
<p style="text-align: right">[dataExtenso]</p>"""

PADRAO_HISTORICO = """<h2 style="text-align: center">HISTÓRICO ESCOLAR</h2>
<p><strong>Aluno:</strong> [nomeAluno] — <strong>Código:</strong> [codigoUnicoAluno]</p>
<p><strong>Nascimento:</strong> [dataNascimentoAluno] — <strong>Naturalidade:</strong> [naturalidadeAluno] — <strong>Nacionalidade:</strong> [nacionalidadeAluno]</p>
<p><strong>Filiação:</strong> [nomeMae] e [nomePai]</p>
<div>[tabelaHistorico]</div>
<p><strong>Situação do histórico:</strong> [situacaoHistorico] — <strong>Período:</strong> [periodoHistorico] — <strong>Carga horária total:</strong> [cargaHorariaTotal]</p>
<p style="text-align: right">[nomeEscola], [dataExtenso].</p>
<p style="text-align: center">_______________________________<br>Secretaria</p>"""

PADROES = {
    "BOLETIM_ESCOLAR": ("Boletim escolar (padrão)", PADRAO_BOLETIM),
    "HISTORICO_ESCOLAR": ("Histórico escolar (padrão)", PADRAO_HISTORICO),
}


def modelo_para(instituicao, tipo):
    """Modelo ativo do tipo (o mais antigo). Sem nenhum modelo cadastrado, cria o padrão do sistema."""
    qs = ModeloDocumento.objects.filter(instituicao=instituicao, tipo=tipo, destinatario=Destinatario.ALUNO)
    ativo = qs.filter(ativo=True).order_by("id").first()
    if ativo:
        return ativo
    if qs.exists():
        raise ValidationError("Não há modelo ativo para este documento. Ative um modelo em Configurações > Documentos.")
    nome, conteudo = PADROES[tipo]
    return ModeloDocumento.objects.create(instituicao=instituicao, nome=nome, tipo=tipo,
                                          destinatario=Destinatario.ALUNO, conteudo=conteudo, usar_cabecalho=True,
                                          ativo=True)


def emitir_boletim(matricula, usuario):
    d = b.liberar_boletim(matricula)
    modelo = modelo_para(matricula.instituicao, "BOLETIM_ESCOLAR")
    return emitir_documento(modelo=modelo, usuario=usuario, aluno=matricula.aluno, via_ficha=True,
                            extra=b.variaveis_boletim(d))


def emitir_historico(aluno, usuario):
    d = b.liberar_historico(aluno)
    modelo = modelo_para(aluno.instituicao, "HISTORICO_ESCOLAR")
    return emitir_documento(modelo=modelo, usuario=usuario, aluno=aluno, via_ficha=True,
                            extra=b.variaveis_historico(d))
