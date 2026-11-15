"""Exportação dos dados pessoais de um titular (aluno) — direito de acesso/portabilidade (LGPD, art. 18).

Reúne o que o sistema guarda sobre o aluno e seus responsáveis. Não inclui segredos, senhas, chaves nem dados de
outras pessoas além dos responsáveis ligados ao aluno. A foto não é embutida (é entregue pela tela da ficha).
"""
from datetime import datetime

from django.core.serializers.json import DjangoJSONEncoder
import json

CAMPOS_ALUNO = ("codigo", "nome", "nome_social", "data_nascimento", "sexo", "cpf", "tipo_documento", "numero_documento",
                "nacionalidade", "naturalidade", "email", "telefone", "celular", "cep", "logradouro", "numero",
                "complemento", "bairro", "municipio", "uf", "nome_mae", "cpf_mae", "telefone_mae", "nome_pai", "cpf_pai",
                "telefone_pai", "responsavel_legal", "nome_responsavel_legal", "cpf_responsavel_legal",
                "telefone_responsavel_legal", "observacoes_filiacao", "ativo", "criado_em")
CAMPOS_RESP = ("nome", "tipo_pessoa", "documento", "email_cobranca", "telefone", "cep", "logradouro", "numero",
               "complemento", "bairro", "municipio", "uf")


def _campos(obj, nomes):
    return {n: getattr(obj, n) for n in nomes}


def dados_do_titular(aluno):
    matriculas = [{"numero": m.numero, "turma": m.turma.nome, "ano_letivo": str(m.ano_letivo or ""), "situacao": m.status,
                   "ingresso": m.data_ingresso, "saida": m.data_saida} for m in aluno.matriculas.select_related("turma", "ano_letivo")]
    responsaveis = [{**_campos(v.responsavel, CAMPOS_RESP), "parentesco": v.parentesco, "inicio": v.inicio, "fim": v.fim}
                    for v in aluno.responsaveis_financeiros.select_related("responsavel")]
    parcelas = [{"descricao": p.descricao, "vencimento": p.vencimento, "valor_devido": p.valor_devido,
                 "valor_pago": p.valor_pago, "situacao": p.status} for p in aluno.parcelas.all()]
    documentos = [{"titulo": d.titulo, "numero": d.numero, "emitido_em": d.emitido_em} for d in aluno.documentos_emitidos.all()]
    externos = [{"ano": h.ano, "serie": h.serie, "escola": h.escola, "resultado": h.resultado,
                 "disciplinas": [{"nome": d.nome, "media": d.media, "carga_horaria": d.carga_horaria}
                                 for d in h.disciplinas.all()]} for h in aluno.historicos_externos.all()] \
        if hasattr(aluno, "historicos_externos") else []
    resultados = [{"disciplina": r.alocacao.disciplina.nome, "ano": r.alocacao.ano_letivo.ano, "media_final": r.media_final,
                   "frequencia": r.frequencia, "resultado": r.resultado}
                  for r in _resultados(aluno)]
    return {"gerado_em": datetime.now().isoformat(timespec="seconds"), "instituicao": str(aluno.instituicao),
            "aluno": _campos(aluno, CAMPOS_ALUNO), "responsaveis": responsaveis, "matriculas": matriculas,
            "parcelas": parcelas, "documentos_emitidos": documentos, "historico_de_outras_escolas": externos,
            "resultados_finais": resultados}


def _resultados(aluno):
    from apps.diario.models import ResultadoFinal
    return ResultadoFinal.objects.filter(matricula__aluno=aluno).select_related("alocacao__disciplina", "alocacao__ano_letivo")


def para_json(dados):
    return json.dumps(dados, cls=DjangoJSONEncoder, ensure_ascii=False, indent=2)
