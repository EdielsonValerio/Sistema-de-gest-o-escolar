from datetime import date
from decimal import Decimal
from itertools import count

from django.contrib.auth.models import Group, User

from apps.academic.models import AnoLetivo, SerieOfertada, SerieReferencia, Turma
from apps.academic.services import carregar_series_padrao, gerar_codigo_turma
from apps.accounts.models import PerfilUsuario
from apps.core.models import Instituicao
from apps.finance.models import PlanoFinanceiro
from apps.students.models import Aluno
from apps.students.services import cadastrar_aluno

_n = count(1)


def criar_instituicao(nome="Escola Teste"):
    return Instituicao.objects.create(nome=nome)


def criar_usuario(inst, grupo=None, username=None, superuser=False):
    username = username or f"user{next(_n)}"
    u = User.objects.create_user(username, password="senha-de-teste-123", is_superuser=superuser)
    PerfilUsuario.objects.create(user=u, instituicao=inst)
    if grupo:
        u.groups.add(Group.objects.get(name=grupo))
    return u


def criar_aluno(inst, usuario, nome=None, **kw):
    n = next(_n)
    a = Aluno(instituicao=inst, nome=nome or f"Aluno {n}", data_nascimento=date(2015, 5, 10), **kw)
    return cadastrar_aluno(a, usuario)


def criar_ano(inst, ano=2027):
    obj, _ = AnoLetivo.objects.get_or_create(
        instituicao=inst, ano=ano,
        defaults=dict(nome=f"Ano letivo {ano}", data_inicio=date(ano, 2, 1), data_fim=date(ano, 12, 15),
                      situacao=AnoLetivo.Situacao.ATIVO))
    return obj


def criar_turma_oficial(inst, ano=None, codigo_serie="EF1-01", numero=1, nome=None, **kw):
    carregar_series_padrao()
    ano = ano or criar_ano(inst)
    ref = SerieReferencia.objects.get(codigo=codigo_serie)
    so, _ = SerieOfertada.objects.get_or_create(ano_letivo=ano, serie_referencia=ref)
    t = Turma(instituicao=inst, codigo=gerar_codigo_turma(inst), numero=numero, nome=nome or f"{ref.nome} {numero}",
              tipo=Turma.Tipo.OFICIAL, ano_letivo=ano, serie_ofertada=so, situacao=Turma.Situacao.ATIVA, **kw)
    t.full_clean(exclude=["codigo"])
    t.save()
    return t


def criar_turma_nao_oficial(inst, numero=1, nome="Reforço de Matemática", ano=2027,
                            inicio=date(2027, 3, 1), fim=date(2027, 5, 31), **kw):
    t = Turma(instituicao=inst, codigo=gerar_codigo_turma(inst), numero=numero, nome=nome,
              tipo=Turma.Tipo.NAO_OFICIAL, ano_referencia=ano, data_inicio=inicio, data_fim=fim,
              situacao=Turma.Situacao.ATIVA, **kw)
    t.full_clean(exclude=["codigo"])
    t.save()
    return t


def criar_plano(inst, turma, nome="Plano anual", total="6000.00", parcelas=12, primeira=date(2027, 1, 10), dia=10,
                desconto="0", **kw):
    return PlanoFinanceiro.objects.create(
        instituicao=inst, nome=nome, turma=turma, valor_total=Decimal(total), quantidade_parcelas=parcelas,
        data_primeira_parcela=primeira, dia_vencimento=dia, desconto_padrao_percentual=Decimal(desconto), **kw)
