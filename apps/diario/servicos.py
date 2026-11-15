"""Regras do Diário de Classe (alocação, aulas, frequência, notas, fechamento). Views só chamam estas funções."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from apps.audit.services import registrar
from apps.enrollments.models import Matricula

from . import formulas
from .models import (AlocacaoProfessor, Aula, Avaliacao, ComponenteNota, ConfiguracaoDiario, Disciplina, FechamentoDiario,
                     Frequencia, ModeloAvaliacao, Nota, PeriodoLetivo)

DOIS = Decimal("0.01")


def configuracao(escola):
    cfg, _ = ConfiguracaoDiario.objects.get_or_create(instituicao=escola)
    return cfg


def professores_da_escola(escola, so_ativos=True):
    qs = User.objects.filter(perfil__instituicao=escola, groups__name="Professor", is_superuser=False)
    return (qs.filter(is_active=True) if so_ativos else qs).select_related("perfil").distinct().order_by("first_name", "perfil__login")


def nome_do(user):
    return user.get_full_name() or user.perfil.login


def _audit(escola, user, acao, entidade, obj_id, descricao, dados=None):
    registrar(instituicao=escola, usuario=user, acao=acao, entidade=entidade, entidade_id=obj_id, descricao=descricao,
              dados=dados)


# --------------------------------------------------------------------------- alocação
def validar_alocacao(escola, ano, turma, disciplina, professor):
    if ano.instituicao_id != escola.pk or turma.instituicao_id != escola.pk or disciplina.instituicao_id != escola.pk:
        raise ValidationError("Ano letivo, turma e disciplina devem pertencer à escola.")
    perfil = getattr(professor, "perfil", None)
    if perfil is None or perfil.instituicao_id != escola.pk:
        raise ValidationError("O professor deve pertencer à mesma escola.")
    if not professor.is_active or not professor.groups.filter(name="Professor").exists():
        raise ValidationError("Somente usuários ativos com perfil Professor podem ser alocados.")
    if turma.ano_letivo_id and turma.ano_letivo_id != ano.pk:
        raise ValidationError("A turma não pertence ao ano letivo selecionado.")
    if not disciplina.ativa:
        raise ValidationError("A disciplina está inativa.")


@transaction.atomic
def alocar(*, escola, ano, turma, disciplina, professor, por):
    validar_alocacao(escola, ano, turma, disciplina, professor)
    if AlocacaoProfessor.objects.filter(turma=turma, disciplina=disciplina, professor=professor,
                                        situacao=AlocacaoProfessor.Situacao.ATIVA).exists():
        raise ValidationError("Este professor já está alocado nesta turma e disciplina.")
    a = AlocacaoProfessor.objects.create(instituicao=escola, ano_letivo=ano, turma=turma, disciplina=disciplina,
                                         professor=professor, criada_por=por)
    _audit(escola, por, "ALOCACAO_CRIADA", "AlocacaoProfessor", a.pk, f"Alocação: {a}")
    return a


@transaction.atomic
def encerrar_alocacao(alocacao, por):
    if alocacao.situacao != AlocacaoProfessor.Situacao.ATIVA:
        raise ValidationError("A alocação já está encerrada.")
    alocacao.situacao = AlocacaoProfessor.Situacao.ENCERRADA
    alocacao.data_fim = timezone.localdate()
    alocacao.save(update_fields=["situacao", "data_fim"])
    _audit(alocacao.instituicao, por, "ALOCACAO_ENCERRADA", "AlocacaoProfessor", alocacao.pk, f"Alocação encerrada: {alocacao}")


@transaction.atomic
def trocar_professor(alocacao, novo, por):
    """Alterar = encerra a alocação atual (mantendo o histórico do professor anterior) e cria outra."""
    antigo = alocacao.professor
    encerrar_alocacao(alocacao, por)
    nova = alocar(escola=alocacao.instituicao, ano=alocacao.ano_letivo, turma=alocacao.turma,
                  disciplina=alocacao.disciplina, professor=novo, por=por)
    _audit(alocacao.instituicao, por, "ALOCACAO_PROFESSOR_ALTERADO", "AlocacaoProfessor", nova.pk,
           "Professor da turma/disciplina alterado", {"de": antigo.pk, "para": novo.pk})
    return nova


# --------------------------------------------------------------------------- períodos e fechamento
def modelo_da_alocacao(alocacao):
    """Modelo de avaliação PUBLICADO que vale para a turma (série própria > modelo geral da escola/ano); None = regra padrão."""
    if hasattr(alocacao, "_modelo_cache"):
        return alocacao._modelo_cache
    qs = ModeloAvaliacao.objects.filter(instituicao=alocacao.instituicao, ano_letivo=alocacao.ano_letivo,
                                        situacao=ModeloAvaliacao.Situacao.PUBLICADO)
    so = alocacao.turma.serie_ofertada
    modelo = qs.filter(series=so.serie_referencia_id).first() if so else None
    modelo = modelo or qs.filter(series__isnull=True).first()
    alocacao._modelo_cache = modelo
    return modelo


def periodos_da_alocacao(alocacao):
    """Fases do modelo vigente da turma ou, sem modelo, os períodos gerais do ano letivo."""
    modelo = modelo_da_alocacao(alocacao)
    base = PeriodoLetivo.objects.filter(instituicao=alocacao.instituicao, ano_letivo=alocacao.ano_letivo)
    return (base.filter(modelo=modelo) if modelo else base.filter(modelo__isnull=True)).order_by("ordem")


def criterios_da_alocacao(alocacao):
    """(média mínima de aprovação, frequência mínima %, modelo|None) que valem para a turma."""
    modelo = modelo_da_alocacao(alocacao)
    if modelo:
        return modelo.media_aprovacao, modelo.frequencia_minima, modelo
    cfg = configuracao(alocacao.instituicao)
    return cfg.media_aprovacao, cfg.frequencia_minima, None


def periodo_da_data(alocacao, data):
    return periodos_da_alocacao(alocacao).filter(data_inicio__lte=data, data_fim__gte=data).first()


def diario_fechado(alocacao, periodo):
    return bool(periodo and FechamentoDiario.objects.filter(alocacao=alocacao, periodo=periodo, fechado=True).exists())


def _exigir_aberto(alocacao, periodo):
    if diario_fechado(alocacao, periodo):
        raise ValidationError("O diário deste período está fechado. Peça à coordenação para reabri-lo.")


def _exigir_ativa(alocacao):
    if alocacao.situacao != AlocacaoProfessor.Situacao.ATIVA:
        raise ValidationError("A alocação está encerrada: não é possível novos lançamentos.")


@transaction.atomic
def fechar_diario(alocacao, periodo, por):
    if periodo.ano_letivo_id != alocacao.ano_letivo_id or periodo.instituicao_id != alocacao.instituicao_id:
        raise ValidationError("Período inválido para esta alocação.")
    if Aula.objects.filter(alocacao=alocacao, periodo=periodo).exclude(status=Aula.Status.FINALIZADA).exists():
        raise ValidationError("Há aulas em rascunho ou com correção pendente neste período.")
    f, _ = FechamentoDiario.objects.get_or_create(instituicao=alocacao.instituicao, alocacao=alocacao, periodo=periodo)
    f.fechado, f.fechado_em, f.fechado_por, f.motivo_reabertura = True, timezone.now(), por, ""
    f.save()
    _audit(alocacao.instituicao, por, "DIARIO_FECHADO", "FechamentoDiario", f.pk, f"Diário fechado: {alocacao} — {periodo}")
    from .boletim import sincronizar_alocacao  # libera a nota da fase ao boletim
    sincronizar_alocacao(alocacao)
    return f


@transaction.atomic
def reabrir_diario(fechamento, motivo, por):
    if not motivo.strip():
        raise ValidationError("Informe o motivo da reabertura.")
    fechamento.fechado, fechamento.reaberto_por, fechamento.motivo_reabertura = False, por, motivo.strip()[:255]
    fechamento.save()
    _audit(fechamento.instituicao, por, "DIARIO_REABERTO", "FechamentoDiario", fechamento.pk,
           f"Diário reaberto: {fechamento.alocacao} — {fechamento.periodo}")
    from .boletim import sincronizar_alocacao  # retira a fase do boletim até novo fechamento
    sincronizar_alocacao(fechamento.alocacao)


# --------------------------------------------------------------------------- aulas e frequência
def matriculas_da_turma(turma):
    return (Matricula.objects.filter(instituicao=turma.instituicao, turma=turma, status=Matricula.Status.ATIVA)
            .select_related("aluno").order_by("aluno__nome"))


@transaction.atomic
def salvar_aula(*, alocacao, aula, dados, presencas, finalizar, por):
    """Cria/atualiza a aula e a chamada. `presencas`: {matricula_id: 'P'|'F'|'J'}."""
    _exigir_ativa(alocacao)
    if aula is not None and aula.status == Aula.Status.FINALIZADA:
        raise ValidationError("Aula finalizada não pode ser alterada. Peça uma correção à coordenação.")
    periodo = periodo_da_data(alocacao, dados["data"])
    _exigir_aberto(alocacao, periodo)
    novo = aula is None
    if novo:
        aula = Aula(instituicao=alocacao.instituicao, alocacao=alocacao, turma=alocacao.turma,
                    disciplina=alocacao.disciplina, professor=alocacao.professor)
    for campo in ("data", "horario_inicio", "horario_fim", "quantidade", "conteudo", "metodologia", "atividades", "observacoes"):
        setattr(aula, campo, dados.get(campo))
    aula.periodo = periodo
    matriculas = {m.pk: m for m in matriculas_da_turma(alocacao.turma)}
    if finalizar:
        if not (aula.conteudo or "").strip():
            raise ValidationError("Informe o conteúdo ministrado para finalizar a aula.")
        faltando = [m for m in matriculas if m not in presencas]
        if faltando:
            raise ValidationError("Registre a frequência de todos os alunos para finalizar a aula.")
        aula.status, aula.finalizada_em, aula.motivo_correcao = Aula.Status.FINALIZADA, timezone.now(), ""
    elif not novo and aula.status == Aula.Status.CORRECAO:
        pass  # permanece "correção solicitada" até ser finalizada de novo
    aula.save()
    for mid, sit in presencas.items():
        if mid not in matriculas:
            raise ValidationError("Aluno não pertence à turma.")  # nunca aceita aluno de fora da turma
        if sit not in dict(Frequencia.Situacao.choices):
            raise ValidationError("Situação de frequência inválida.")
        Frequencia.objects.update_or_create(aula=aula, matricula_id=mid,
                                            defaults={"situacao": sit, "instituicao": alocacao.instituicao})
    Frequencia.objects.filter(aula=aula).exclude(matricula_id__in=list(presencas)).delete()
    if finalizar:
        _audit(alocacao.instituicao, por, "AULA_FINALIZADA", "Aula", aula.pk, f"Aula finalizada: {alocacao}")
    return aula


@transaction.atomic
def solicitar_correcao(aula, motivo, por):
    if aula.status != Aula.Status.FINALIZADA:
        raise ValidationError("Só é possível solicitar correção de aula finalizada.")
    if not motivo.strip():
        raise ValidationError("Informe o motivo da correção.")
    if diario_fechado(aula.alocacao, aula.periodo):
        raise ValidationError("O diário do período está fechado: reabra-o primeiro.")
    aula.status, aula.motivo_correcao = Aula.Status.CORRECAO, motivo.strip()[:255]
    aula.save(update_fields=["status", "motivo_correcao"])
    _audit(aula.instituicao, por, "AULA_CORRECAO_SOLICITADA", "Aula", aula.pk, "Correção de aula solicitada")


# --------------------------------------------------------------------------- avaliações e notas
@transaction.atomic
def criar_avaliacao(*, alocacao, dados, por):
    _exigir_ativa(alocacao)
    periodo, tipo = dados["periodo"], dados.get("tipo", Avaliacao.Tipo.NORMAL)
    componente = dados.get("componente")
    modelo = modelo_da_alocacao(alocacao)
    periodos = list(periodos_da_alocacao(alocacao))
    if periodo.instituicao_id != alocacao.instituicao_id or periodo.pk not in [p.pk for p in periodos]:
        raise ValidationError("Período inválido para esta turma.")
    if modelo is None:
        if tipo == Avaliacao.Tipo.REC_FINAL:
            raise ValidationError("A recuperação final só existe em escolas com modelo de avaliação configurado.")
        componente = None
    else:
        if tipo == Avaliacao.Tipo.RECUPERACAO and not modelo.usa_rec_parcial:
            raise ValidationError("Este modelo de avaliação não tem recuperação de fase.")
        if tipo == Avaliacao.Tipo.REC_FINAL:
            if not modelo.usa_rec_final:
                raise ValidationError("Este modelo de avaliação não tem recuperação final.")
            periodo = periodos[-1]  # a recuperação final fica na última fase
        if tipo == Avaliacao.Tipo.NORMAL:
            tem = periodo.componentes.exists()
            if tem and (componente is None or componente.fase_id != periodo.pk):
                raise ValidationError("Escolha o componente da nota (ex.: Prova, Atividade) desta fase.")
            if not tem:
                componente = None
        else:
            componente = None
    _exigir_aberto(alocacao, periodo)
    a = Avaliacao.objects.create(instituicao=alocacao.instituicao, alocacao=alocacao, periodo=periodo, componente=componente,
                                 titulo=dados["titulo"], tipo=tipo, data=dados["data"],
                                 nota_maxima=dados["nota_maxima"], peso=dados["peso"])
    _audit(alocacao.instituicao, por, "AVALIACAO_CRIADA", "Avaliacao", a.pk, f"Avaliação: {a.titulo}")
    return a


@transaction.atomic
def lancar_notas(avaliacao, notas, por):
    """`notas`: {matricula_id: Decimal|None}. Valida faixa 0..nota_maxima e pertencimento à turma."""
    _exigir_ativa(avaliacao.alocacao)
    _exigir_aberto(avaliacao.alocacao, avaliacao.periodo)
    validos = {m.pk for m in matriculas_da_turma(avaliacao.alocacao.turma)}
    for mid, valor in notas.items():
        if mid not in validos:
            raise ValidationError("Aluno não pertence à turma.")
        if valor is not None and not (Decimal("0") <= valor <= avaliacao.nota_maxima):
            raise ValidationError(f"A nota deve estar entre 0 e {avaliacao.nota_maxima}.")
    for mid, valor in notas.items():
        Nota.objects.update_or_create(avaliacao=avaliacao, matricula_id=mid,
                                      defaults={"valor": valor, "instituicao": avaliacao.instituicao})
    _audit(avaliacao.instituicao, por, "NOTAS_LANCADAS", "Avaliacao", avaliacao.pk, f"Notas lançadas: {avaliacao.titulo}",
           {"quantidade": len(notas)})


# --------------------------------------------------------------------------- modelos de avaliação (administrador do sistema)
from datetime import date, timedelta  # noqa: E402

_ROTULO_FASE = {"BIMESTRAL": ("Bimestre", 4), "TRIMESTRAL": ("Trimestre", 3), "SEMESTRAL": ("Semestre", 2),
                "MENSAL": ("Mês", 10)}


def notas_lancadas(modelo):
    return Nota.objects.filter(avaliacao__periodo__modelo=modelo, valor__isnull=False).exists()


def estrutura_travada(modelo):
    """Publicado e com notas lançadas: fases, componentes, ano e séries não mudam (parâmetros ainda podem ser ajustados)."""
    return modelo.situacao == ModeloAvaliacao.Situacao.PUBLICADO and notas_lancadas(modelo)


def _exigir_rascunho_ou_livre(modelo):
    if estrutura_travada(modelo):
        raise ValidationError("Há notas lançadas com este modelo: a estrutura (fases e componentes) não pode mais mudar. "
                              "Duplique o modelo para outro ano letivo ou despublique-o antes de lançar notas.")


@transaction.atomic
def gerar_fases(modelo, por):
    _exigir_rascunho_ou_livre(modelo)
    if modelo.fases.exists():
        raise ValidationError("O modelo já tem fases. Remova-as antes de gerar novamente.")
    rotulo, n = _ROTULO_FASE[modelo.tipo_fase]
    ano = modelo.ano_letivo
    dias = (ano.data_fim - ano.data_inicio).days + 1
    passo, inicio = dias // n, ano.data_inicio
    for i in range(1, n + 1):
        fim = ano.data_fim if i == n else inicio + timedelta(days=passo - 1)
        PeriodoLetivo.objects.create(instituicao=modelo.instituicao, ano_letivo=ano, modelo=modelo, ordem=i,
                                     nome=f"{i}º {rotulo}", data_inicio=inicio, data_fim=fim)
        inicio = fim + timedelta(days=1)
    _audit(modelo.instituicao, por, "MODELO_AVALIACAO_FASES_GERADAS", "ModeloAvaliacao", modelo.pk,
           f"{n} fases geradas ({modelo.tipo_fase})")


def validar_modelo(modelo):
    """(erros, avisos) do modelo. Só publica sem erros."""
    from . import motor
    erros, avisos = [], []
    fases = list(modelo.fases.prefetch_related("componentes").order_by("ordem"))
    if not fases:
        erros.append("Defina as fases avaliativas (use \"Gerar fases\").")
    for f in fases:
        comps = list(f.componentes.all())
        for c in comps:
            if not formulas.sigla_valida(c.sigla):
                erros.append(f"{f.nome}: sigla inválida \"{c.sigla}\" (use letras maiúsculas/números, sem nomes reservados).")
        if f.formula:
            if not comps:
                erros.append(f"{f.nome}: a fórmula exige componentes (ex.: ATV, PROVA).")
            else:
                e = formulas.validar(f.formula, motor.nomes_formula_fase(comps))
                if e:
                    erros.append(f"{f.nome}: {e}")
        elif comps:
            total = sum((c.valor_maximo for c in comps), Decimal(0))
            if total != modelo.nota_maxima:
                avisos.append(f"{f.nome}: os componentes somam {total} e a nota máxima é {modelo.nota_maxima}.")
        if f.data_fim < f.data_inicio:
            erros.append(f"{f.nome}: término antes do início.")
    if modelo.formula_final:
        e = formulas.validar(modelo.formula_final, motor.nomes_formula_final(len(fases)))
        if e:
            erros.append(f"Fórmula da média anual: {e}")
    if modelo.usa_rec_final and modelo.formula_rec_final:
        e = formulas.validar(modelo.formula_rec_final, motor.NOMES_REC_FINAL)
        if e:
            erros.append(f"Fórmula da recuperação final: {e}")
    if modelo.media_aprovacao > modelo.nota_maxima:
        erros.append("A nota mínima de aprovação não pode ser maior que a nota máxima.")
    if modelo.ano_letivo.instituicao_id != modelo.instituicao_id:
        erros.append("Ano letivo de outra escola.")
    series = set(modelo.series.values_list("pk", flat=True)) if modelo.pk else set()
    for outro in ModeloAvaliacao.objects.filter(instituicao=modelo.instituicao, ano_letivo=modelo.ano_letivo,
                                                situacao=ModeloAvaliacao.Situacao.PUBLICADO).exclude(pk=modelo.pk):
        os_ = set(outro.series.values_list("pk", flat=True))
        if (not series and not os_) or (series & os_):
            erros.append(f"Conflito com o modelo publicado \"{outro.nome}\": a mesma série (ou \"todas\") já tem modelo neste ano.")
    return erros, avisos


@transaction.atomic
def publicar_modelo(modelo, por):
    erros, _ = validar_modelo(modelo)
    if erros:
        raise ValidationError(erros)
    modelo.situacao = ModeloAvaliacao.Situacao.PUBLICADO
    modelo.publicado_em = timezone.now()
    modelo.save(update_fields=["situacao", "publicado_em"])
    _audit(modelo.instituicao, por, "MODELO_AVALIACAO_PUBLICADO", "ModeloAvaliacao", modelo.pk, f"Modelo publicado: {modelo}")


@transaction.atomic
def despublicar_modelo(modelo, por):
    if notas_lancadas(modelo):
        raise ValidationError("Há notas lançadas com este modelo; não é possível despublicá-lo.")
    modelo.situacao = ModeloAvaliacao.Situacao.RASCUNHO
    modelo.save(update_fields=["situacao"])
    _audit(modelo.instituicao, por, "MODELO_AVALIACAO_DESPUBLICADO", "ModeloAvaliacao", modelo.pk, f"Modelo despublicado: {modelo}")


def _mesmo_dia(d, ano):
    try:
        return d.replace(year=ano)
    except ValueError:  # 29/02
        return d.replace(year=ano, day=28)


@transaction.atomic
def duplicar_modelo(modelo, ano_destino, nome, por):
    """Cópia em RASCUNHO (fases, componentes, fórmulas e séries) para outro ano letivo da mesma escola."""
    if ano_destino.instituicao_id != modelo.instituicao_id:
        raise ValidationError("O ano letivo deve ser da mesma escola.")
    if ModeloAvaliacao.objects.filter(instituicao=modelo.instituicao, ano_letivo=ano_destino, nome=nome).exists():
        raise ValidationError("Já existe um modelo com este nome neste ano letivo.")
    campos = ("tipo_fase", "nota_maxima", "media_aprovacao", "frequencia_minima", "casas_decimais", "arredondamento",
              "formula_final", "usa_rec_parcial", "regra_rec_parcial", "usa_rec_final", "formula_rec_final",
              "media_aprovacao_rec_final")
    novo = ModeloAvaliacao.objects.create(instituicao=modelo.instituicao, ano_letivo=ano_destino, nome=nome,
                                          **{c: getattr(modelo, c) for c in campos})
    novo.series.set(modelo.series.all())
    for f in modelo.fases.prefetch_related("componentes").order_by("ordem"):
        nf = PeriodoLetivo.objects.create(
            instituicao=f.instituicao, ano_letivo=ano_destino, modelo=novo, nome=f.nome, ordem=f.ordem, peso=f.peso,
            formula=f.formula, data_inicio=_mesmo_dia(f.data_inicio, ano_destino.ano), data_fim=_mesmo_dia(f.data_fim, ano_destino.ano))
        for c in f.componentes.all():
            ComponenteNota.objects.create(instituicao=c.instituicao, fase=nf, nome=c.nome, sigla=c.sigla,
                                          valor_maximo=c.valor_maximo, agregacao=c.agregacao, ordem=c.ordem)
    _audit(modelo.instituicao, por, "MODELO_AVALIACAO_DUPLICADO", "ModeloAvaliacao", novo.pk, f"Modelo duplicado de {modelo}")
    return novo


PRESETS = {
    "bim_simples": ("Bimestral simples — média ponderada das fases", "BIMESTRAL", [], False, False),
    "bim_atv_prova": ("Bimestral — atividade (4,0) + prova (6,0), com recuperação de fase e final", "BIMESTRAL",
                      [("Atividades", "ATV", "4", "SOMA"), ("Prova", "PROVA", "6", "SOMA")], True, True),
    "tri_atv_prova": ("Trimestral — atividade (4,0) + prova (6,0), com recuperação final", "TRIMESTRAL",
                      [("Atividades", "ATV", "4", "SOMA"), ("Prova", "PROVA", "6", "SOMA")], False, True),
    "mensal_simples": ("Mensal simples — média ponderada das fases", "MENSAL", [], False, False),
}


@transaction.atomic
def criar_de_preset(escola, ano, nome, preset, por):
    """Cria um modelo em RASCUNHO a partir de um modelo pronto (fases e componentes já montados)."""
    if preset not in PRESETS:
        raise ValidationError("Modelo pronto inválido.")
    if ano.instituicao_id != escola.pk:
        raise ValidationError("O ano letivo deve ser da escola.")
    nome = (nome or "").strip() or PRESETS[preset][0][:80]
    if ModeloAvaliacao.objects.filter(instituicao=escola, ano_letivo=ano, nome=nome).exists():
        raise ValidationError("Já existe um modelo com este nome neste ano letivo.")
    _, tipo, comps, rec_parcial, rec_final = PRESETS[preset]
    modelo = ModeloAvaliacao.objects.create(instituicao=escola, ano_letivo=ano, nome=nome, tipo_fase=tipo,
                                            usa_rec_parcial=rec_parcial, usa_rec_final=rec_final)
    gerar_fases(modelo, por)
    for fase in modelo.fases.all():
        for i, (cn, sigla, valor, ag) in enumerate(comps, 1):
            ComponenteNota.objects.create(instituicao=escola, fase=fase, nome=cn, sigla=sigla, valor_maximo=Decimal(valor),
                                          agregacao=ag, ordem=i)
    _audit(escola, por, "MODELO_AVALIACAO_CRIADO", "ModeloAvaliacao", modelo.pk, f"Modelo criado a partir do modelo pronto {preset}")
    return modelo
