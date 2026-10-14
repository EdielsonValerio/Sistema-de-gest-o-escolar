"""Lista predefinida de tipos de documento (códigos estáveis; o rótulo pode ser ajustado sem afetar os dados)."""

TIPOS_DOCUMENTO = [
    # Secretaria / acadêmicos
    ("DECL_MATRICULA", "Declaração de matrícula"),
    ("DECL_ESCOLARIDADE", "Declaração de escolaridade"),
    ("DECL_FREQUENCIA", "Declaração de frequência"),
    ("DECL_CONCLUSAO", "Declaração de conclusão de curso ou etapa"),
    ("HISTORICO_ESCOLAR", "Histórico escolar"),
    ("BOLETIM_ESCOLAR", "Boletim escolar"),
    ("FICHA_INDIVIDUAL", "Ficha individual do aluno"),
    ("ATESTADO_TRANSFERENCIA", "Atestado de transferência"),
    ("GUIA_TRANSFERENCIA", "Guia de transferência"),
    ("CERTIFICADO_CONCLUSAO", "Certificado de conclusão"),
    ("DIPLOMA", "Diploma"),
    ("DECL_APROVACAO", "Declaração de aprovação"),
    ("DECL_REPROVACAO", "Declaração de reprovação"),
    ("CONTRATO_SERVICOS", "Contrato de prestação de serviços educacionais"),
    ("REQ_MATRICULA", "Requerimento de matrícula"),
    ("REQ_REMATRICULA", "Requerimento de rematrícula"),
    ("COMP_MATRICULA", "Comprovante de matrícula"),
    ("COMP_REMATRICULA", "Comprovante de rematrícula"),
    # Financeiro
    ("DECL_QUITACAO", "Declaração de quitação de débitos escolares"),
    ("DECL_ADIMPLENCIA", "Declaração de adimplência financeira"),
    ("RECIBO_MENSALIDADE", "Recibo de pagamento de mensalidade"),
    ("COMP_TAXAS", "Comprovante de pagamento de taxas e serviços"),
    ("DEMONSTRATIVO_DEBITOS", "Demonstrativo de débitos em aberto"),
    ("DECL_IMPOSTO_RENDA", "Declaração para fins de Imposto de Renda"),
    # Solicitações / protocolos
    ("SEGUNDA_VIA", "Solicitação de segunda via de documentos"),
    ("PROTOCOLO_DOCUMENTOS", "Protocolo de solicitação de documentos"),
    # Acompanhamento e comparecimento
    ("DECL_ACOMPANHAMENTO", "Declaração de acompanhamento escolar"),
    ("DECL_COMPARECIMENTO_REUNIAO", "Declaração de comparecimento à reunião escolar"),
    ("DECL_COMPARECIMENTO_PEDAGOGICO", "Declaração de comparecimento para atendimento pedagógico"),
    ("DECL_RESP_FINANCEIRO", "Declaração de responsável financeiro"),
    ("DECL_VINCULO_RESPONSAVEL", "Declaração de vínculo do responsável com o aluno"),
    # Autorizações e termos
    ("AUT_RETIRADA", "Autorização para retirada do aluno"),
    ("AUT_PASSEIOS", "Autorização para participação em passeios e atividades externas"),
    ("AUT_IMAGEM_VOZ", "Autorização para uso de imagem e voz"),
    ("TERMO_REGIMENTO", "Termo de ciência do regimento escolar"),
    ("TERMO_RESPONSABILIDADE", "Termo de responsabilidade"),
    ("COMUNICADO_OCORRENCIAS", "Comunicado formal de ocorrências escolares"),
    # Pedagógicos
    ("RELATORIO_DESENVOLVIMENTO", "Relatório de desenvolvimento do aluno"),
    ("PARECER_DESCRITIVO", "Parecer descritivo pedagógico"),
    ("RELATORIO_ACOMP_INDIVIDUAL", "Relatório de acompanhamento individual"),
    ("PDI", "Plano de Desenvolvimento Individual (PDI)"),
    ("RELATORIO_RECUPERACAO", "Relatório de recuperação ou reforço escolar"),
    ("REGISTRO_DISCIPLINAR", "Registro de ocorrências disciplinares"),
    ("ADVERTENCIA", "Advertência escolar"),
    ("NOTIFICACAO_RESPONSAVEIS", "Notificação aos responsáveis"),
    ("RELATORIO_FREQUENCIA", "Relatório de acompanhamento da frequência"),
    ("REGISTRO_REUNIOES", "Registro de reuniões pedagógicas com os responsáveis"),
    # Saúde e segurança
    ("FICHA_SAUDE", "Ficha de saúde do aluno"),
    ("FICHA_ALERGIAS", "Ficha de informações sobre alergias e restrições alimentares"),
    ("AUT_MEDICAMENTOS", "Autorização para administração de medicamentos"),
    ("AUT_EVENTOS", "Autorização para participação em eventos"),
    ("AUT_EXCURSOES", "Autorização para excursões e visitas técnicas"),
    ("TERMO_SEGURANCA", "Termo de ciência sobre normas de segurança"),
    ("REGISTRO_ACIDENTES", "Registro de acidentes ou incidentes escolares"),
    ("COMP_ENTREGA_ALUNO", "Comprovante de entrega do aluno ao responsável autorizado"),
    # Solicitações dos responsáveis
    ("SOL_TRANSFERENCIA", "Solicitação de transferência"),
    ("SOL_HISTORICO", "Solicitação de histórico escolar"),
    ("SOL_DECLARACAO", "Solicitação de declaração"),
    ("SOL_REVISAO_NOTAS", "Solicitação de revisão de notas ou resultados"),
    ("PROTOCOLO_ATENDIMENTO", "Protocolo de atendimento da secretaria"),
    ("RESPOSTA_FORMAL", "Resposta formal a solicitações dos responsáveis"),
]

CHOICES_TIPO = TIPOS_DOCUMENTO
ROTULO_TIPO = dict(TIPOS_DOCUMENTO)
