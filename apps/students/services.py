from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import registrar
from apps.core import numbering

from .models import Aluno, AlunoResponsavelFinanceiro as Vinculo, Responsavel


@transaction.atomic
def cadastrar_aluno(aluno, usuario):
    """Atribui o código único (seguro para concorrência) e grava o aluno."""
    aluno.codigo = numbering.gerar_codigo_aluno(aluno.instituicao)
    aluno.criado_por = usuario
    aluno.save()
    registrar(instituicao=aluno.instituicao, usuario=usuario, acao="ALUNO_CADASTRADO", entidade="Aluno",
              entidade_id=aluno.pk, aluno_id=aluno.pk, descricao="Aluno cadastrado",
              dados={"codigo": aluno.codigo})
    return aluno


@transaction.atomic
def responsavel_proprio_aluno(aluno):
    """Responsável financeiro = próprio aluno, copiando (snapshot) dados do cadastro, sem redigitar."""
    r = Responsavel.objects.filter(instituicao=aluno.instituicao, eh_proprio_aluno=True,
                                   vinculos_alunos__aluno=aluno).first()
    if r:
        return r
    return Responsavel.objects.create(
        instituicao=aluno.instituicao, nome=aluno.nome, documento=aluno.cpf, email_cobranca=aluno.email,
        telefone=aluno.celular or aluno.telefone, cep=aluno.cep, logradouro=aluno.logradouro, numero=aluno.numero,
        complemento=aluno.complemento, bairro=aluno.bairro, municipio=aluno.municipio, uf=aluno.uf,
        eh_proprio_aluno=True)


@transaction.atomic
def definir_responsavel_financeiro(aluno, responsavel, parentesco, usuario):
    """Encerra o vínculo vigente (se outro) e cria o novo, mantendo o histórico."""
    if responsavel.instituicao_id != aluno.instituicao_id:
        raise ValidationError("Responsável de outra instituição.")
    vigente = Vinculo.objects.select_for_update().filter(aluno=aluno, fim__isnull=True).first()
    if vigente and vigente.responsavel_id == responsavel.pk and vigente.parentesco == parentesco:
        return vigente
    hoje = timezone.localdate()
    if vigente:
        vigente.fim = max(hoje, vigente.inicio)
        vigente.save(update_fields=["fim"])
    novo = Vinculo.objects.create(aluno=aluno, responsavel=responsavel, parentesco=parentesco, inicio=hoje,
                                  criado_por=usuario)
    registrar(instituicao=aluno.instituicao, usuario=usuario, acao="RESPONSAVEL_FINANCEIRO_DEFINIDO",
              entidade="Aluno", entidade_id=aluno.pk, aluno_id=aluno.pk,
              descricao="Responsável financeiro definido",
              dados={"responsavel_id": responsavel.pk, "parentesco": parentesco,
                     "anterior_id": vigente.responsavel_id if vigente else None})
    return novo


def _responsavel_de_pessoa(aluno, nome, documento, telefone, usuario_email=""):
    """Reaproveita (por documento) ou cria um responsável a partir de dados de filiação do aluno."""
    if documento:
        r = Responsavel.objects.filter(instituicao=aluno.instituicao, documento=documento).first()
        if r:
            return r
    return Responsavel.objects.create(instituicao=aluno.instituicao, nome=nome, documento=documento,
                                      telefone=telefone, email_cobranca=usuario_email,
                                      cep=aluno.cep, logradouro=aluno.logradouro, numero=aluno.numero,
                                      complemento=aluno.complemento, bairro=aluno.bairro,
                                      municipio=aluno.municipio, uf=aluno.uf)


@transaction.atomic
def aplicar_responsavel_financeiro(aluno, dados, usuario):
    """Interpreta os campos `resp_*` do formulário do aluno e define o responsável financeiro vigente."""
    tipo = dados.get("resp_tipo")
    if not tipo:
        return None
    P = Vinculo.Parentesco
    if tipo == P.PROPRIO:
        r = responsavel_proprio_aluno(aluno)
    elif tipo == P.PAI:
        r = _responsavel_de_pessoa(aluno, aluno.nome_pai, aluno.cpf_pai, aluno.telefone_pai)
    elif tipo == P.MAE:
        r = _responsavel_de_pessoa(aluno, aluno.nome_mae, aluno.cpf_mae, aluno.telefone_mae)
    else:
        r = dados.get("resp_existente")
        if r is None:
            doc = dados.get("resp_documento") or ""
            r = Responsavel.objects.filter(instituicao=aluno.instituicao, documento=doc).first() if doc else None
            if r is None:
                r = Responsavel.objects.create(
                    instituicao=aluno.instituicao, nome=dados["resp_nome"], documento=doc,
                    tipo_pessoa=dados.get("resp_tipo_pessoa") or "PF", email_cobranca=dados.get("resp_email", ""),
                    telefone=dados.get("resp_telefone", ""), cep=dados.get("resp_cep", ""),
                    logradouro=dados.get("resp_logradouro", ""), numero=dados.get("resp_numero", ""),
                    complemento=dados.get("resp_complemento", ""), bairro=dados.get("resp_bairro", ""),
                    municipio=dados.get("resp_municipio", ""), uf=dados.get("resp_uf", ""))
    return definir_responsavel_financeiro(aluno, r, tipo, usuario)
