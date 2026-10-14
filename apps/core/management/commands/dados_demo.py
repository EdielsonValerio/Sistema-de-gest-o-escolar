"""Dados de demonstração, isolados em uma instituição própria (não tocam nos dados reais)."""
from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.academic.models import AnoLetivo, SerieOfertada, SerieReferencia, Turma
from apps.academic.services import carregar_series_padrao, gerar_codigo_turma
from apps.accounts.models import PerfilUsuario
from apps.core.models import ConfiguracaoGeral, Instituicao
from apps.enrollments.services import criar_matricula
from apps.finance.models import PlanoFinanceiro
from apps.finance.services import matricular_com_cobranca, registrar_recebimento
from apps.students.models import Aluno
from apps.students.services import cadastrar_aluno, responsavel_proprio_aluno, definir_responsavel_financeiro

NOME_INST = "Escola Demonstração (dados fictícios)"


class Command(BaseCommand):
    help = ("Cria uma instituição de demonstração com alunos, turmas, planos, matrículas e um recebimento. "
            "Cria/atualiza o usuário 'demo' (perfil Administrador) com a senha informada em --senha.")

    def add_arguments(self, parser):
        parser.add_argument("--senha", required=True, help="Senha do usuário 'demo' (não há senha padrão).")

    @transaction.atomic
    def handle(self, *args, **opts):
        if Instituicao.objects.filter(nome=NOME_INST).exists():
            raise CommandError("Os dados de demonstração já existem (instituição já criada).")
        carregar_series_padrao()
        inst = Instituicao.objects.create(nome=NOME_INST)
        ConfiguracaoGeral.objects.create(instituicao=inst)

        user, _ = User.objects.get_or_create(username="demo")
        user.set_password(opts["senha"])
        user.save()
        PerfilUsuario.objects.update_or_create(user=user, defaults={"instituicao": inst})
        user.groups.add(Group.objects.get(name="Administrador"))

        ano = AnoLetivo.objects.create(instituicao=inst, nome="Ano letivo 2027", ano=2027,
                                       data_inicio=date(2027, 2, 1), data_fim=date(2027, 12, 15),
                                       situacao=AnoLetivo.Situacao.ATIVO)
        turmas = []
        for i, cod in enumerate(("EF1-01", "EF1-02"), start=1):
            ref = SerieReferencia.objects.get(codigo=cod)
            so = SerieOfertada.objects.create(ano_letivo=ano, serie_referencia=ref)
            t = Turma(instituicao=inst, codigo=gerar_codigo_turma(inst), numero=1, nome=f"{ref.nome} A",
                      tipo=Turma.Tipo.OFICIAL, ano_letivo=ano, serie_ofertada=so,
                      situacao=Turma.Situacao.ATIVA, capacidade=25)
            t.full_clean(exclude=["codigo"])
            t.save()
            turmas.append(t)
        reforco = Turma(instituicao=inst, codigo=gerar_codigo_turma(inst), numero=1, nome="Reforço de Matemática",
                        tipo=Turma.Tipo.NAO_OFICIAL, ano_referencia=2027, data_inicio=date(2027, 3, 1),
                        data_fim=date(2027, 5, 31), situacao=Turma.Situacao.ATIVA, categoria="Reforço")
        reforco.full_clean(exclude=["codigo"])
        reforco.save()

        plano12 = PlanoFinanceiro.objects.create(
            instituicao=inst, nome="Anual — 12 parcelas", turma=turmas[0], valor_total=Decimal("6000.00"),
            quantidade_parcelas=12, data_primeira_parcela=date(2027, 1, 10), dia_vencimento=10)
        PlanoFinanceiro.objects.create(
            instituicao=inst, nome="Anual — 10 parcelas", turma=turmas[0], valor_total=Decimal("6000.00"),
            quantidade_parcelas=10, data_primeira_parcela=date(2027, 2, 10), dia_vencimento=10)

        nomes = ["Ana Souza Lima", "Bruno Carvalho Dias", "Carla Menezes Rocha", "Diego Alves Pinto"]
        alunos = []
        for n in nomes:
            a = cadastrar_aluno(Aluno(instituicao=inst, nome=n, data_nascimento=date(2020, 6, 15),
                                      nome_mae=f"Mãe de {n.split()[0]}"), user)
            alunos.append(a)

        resp = responsavel_proprio_aluno(alunos[0])
        definir_responsavel_financeiro(alunos[0], resp, "PROPRIO", user)
        mat, _ = matricular_com_cobranca(aluno=alunos[0], turma=turmas[0], usuario=user, plano=plano12,
                                         responsavel=resp)
        criar_matricula(aluno=alunos[1], turma=turmas[1], usuario=user)
        criar_matricula(aluno=alunos[1], turma=reforco, usuario=user, data_ingresso=date(2027, 3, 1))
        # alunos[2] e [3] ficam sem matrícula, de propósito.

        parcela = mat.parcelas.order_by("numero").first()
        registrar_recebimento(aluno=alunos[0], itens=[(parcela.pk, parcela.saldo)], forma="PIX",
                              valor_recebido=parcela.saldo, usuario=user, token=uuid4(), referencia="DEMO")
        self.stdout.write(self.style.SUCCESS(
            "Dados de demonstração criados. Entre com o usuário 'demo' e a senha informada."))
