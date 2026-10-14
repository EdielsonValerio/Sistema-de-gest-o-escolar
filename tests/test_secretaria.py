from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.academic.models import SerieOfertada, SerieReferencia, Turma
from apps.academic.services import carregar_series_padrao
from apps.core import numbering
from apps.core.models import TipoNumeracao
from apps.students.models import Aluno

from .helpers import (criar_aluno, criar_ano, criar_instituicao, criar_turma_nao_oficial, criar_turma_oficial,
                      criar_usuario)


class AlunoTests(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()
        self.user = criar_usuario(self.inst, "Secretaria")

    def test_aluno_sem_matricula_recebe_codigo_unico(self):
        a1 = criar_aluno(self.inst, self.user)
        a2 = criar_aluno(self.inst, self.user)
        self.assertNotEqual(a1.codigo, a2.codigo)
        self.assertEqual(a1.matriculas.count(), 0)
        self.assertRegex(a1.codigo, r"^\d{4}\d{6}$")
        self.assertTrue(a1.codigo.endswith("000001"))
        self.assertTrue(a2.codigo.endswith("000002"))

    def test_codigo_independe_de_matricula(self):
        a = criar_aluno(self.inst, self.user)
        codigo = a.codigo
        from apps.enrollments.services import criar_matricula
        criar_matricula(aluno=a, turma=criar_turma_oficial(self.inst), usuario=self.user)
        a.refresh_from_db()
        self.assertEqual(a.codigo, codigo)

    def test_cpf_unico_por_instituicao(self):
        criar_aluno(self.inst, self.user, cpf="529.982.247-25")
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_aluno(self.inst, self.user, cpf="529.982.247-25")

    def test_cpf_mascarado(self):
        a = criar_aluno(self.inst, self.user, cpf="52998224725")
        self.assertEqual(a.cpf_mascarado, "***.982.***-25")


class AcademicoTests(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()

    def test_serie_ofertada_unica_por_ano(self):
        carregar_series_padrao()
        ano = criar_ano(self.inst)
        ref = SerieReferencia.objects.get(codigo="EF1-01")
        SerieOfertada.objects.create(ano_letivo=ano, serie_referencia=ref)
        with self.assertRaises(IntegrityError), transaction.atomic():
            SerieOfertada.objects.create(ano_letivo=ano, serie_referencia=ref)

    def test_mesma_serie_em_anos_diferentes(self):
        carregar_series_padrao()
        ref = SerieReferencia.objects.get(codigo="EF1-01")
        for ano in (2026, 2027):
            SerieOfertada.objects.create(ano_letivo=criar_ano(self.inst, ano), serie_referencia=ref)
        self.assertEqual(SerieOfertada.objects.filter(serie_referencia=ref).count(), 2)

    def test_series_padrao_idempotente_e_sem_codigo_oficial(self):
        carregar_series_padrao()
        n = SerieReferencia.objects.count()
        carregar_series_padrao()
        self.assertEqual(SerieReferencia.objects.count(), n)
        self.assertFalse(SerieReferencia.objects.exclude(codigo_oficial="").exists())

    def test_turma_oficial(self):
        t = criar_turma_oficial(self.inst)
        self.assertEqual(t.tipo, Turma.Tipo.OFICIAL)
        self.assertTrue(t.eh_oficial)

    def test_turma_nao_oficial(self):
        t = criar_turma_nao_oficial(self.inst)
        self.assertIsNone(t.serie_ofertada_id)
        self.assertEqual(t.ano_exibicao, 2027)

    def test_turma_oficial_exige_serie(self):
        t = Turma(instituicao=self.inst, codigo="X", numero=1, nome="x", tipo="OFICIAL", ano_letivo=criar_ano(self.inst))
        with self.assertRaises(ValidationError):
            t.full_clean(exclude=["codigo"])

    def test_turma_nao_oficial_exige_periodo(self):
        t = Turma(instituicao=self.inst, codigo="X", numero=1, nome="x", tipo="NAO_OFICIAL", ano_referencia=2027)
        with self.assertRaises(ValidationError):
            t.full_clean(exclude=["codigo"])

    def test_turma_nao_oficial_nao_aceita_serie(self):
        base = criar_turma_oficial(self.inst)
        t = Turma(instituicao=self.inst, codigo="X", numero=9, nome="x", tipo="NAO_OFICIAL", ano_referencia=2027,
                  data_inicio=date(2027, 3, 1), data_fim=date(2027, 4, 1), serie_ofertada=base.serie_ofertada)
        with self.assertRaises(ValidationError):
            t.full_clean(exclude=["codigo"])

    def test_data_termino_anterior_ao_inicio(self):
        with self.assertRaises(ValidationError):
            criar_turma_nao_oficial(self.inst, inicio=date(2027, 6, 1), fim=date(2027, 5, 1))

    def test_ano_letivo_datas_invalidas(self):
        from apps.academic.models import AnoLetivo
        a = AnoLetivo(instituicao=self.inst, nome="x", ano=2030, data_inicio=date(2030, 12, 1), data_fim=date(2030, 1, 1))
        with self.assertRaises(ValidationError):
            a.full_clean()

    def test_constraint_de_banco_impede_turma_incoerente(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Turma.objects.create(instituicao=self.inst, codigo="Z1", numero=1, nome="x", tipo="OFICIAL")

    def test_ano_letivo_unico(self):
        from apps.academic.models import AnoLetivo
        dados = dict(instituicao=self.inst, nome="x", ano=2028, data_inicio=date(2028, 2, 1), data_fim=date(2028, 12, 1))
        AnoLetivo.objects.create(**dados)
        with self.assertRaises(IntegrityError), transaction.atomic():
            AnoLetivo.objects.create(**dados)


class FormatoNumeracaoTests(TestCase):
    def test_variavel_desconhecida_rejeitada(self):
        with self.assertRaises(ValidationError):
            numbering.validar_formato("[Foo][Sequencial]", TipoNumeracao.MATRICULA_OFICIAL)

    def test_exige_sequencial(self):
        with self.assertRaises(ValidationError):
            numbering.validar_formato("[AnoLetivo]", TipoNumeracao.MATRICULA_OFICIAL)

    def test_codigo_nao_permite_variavel_de_turma(self):
        with self.assertRaises(ValidationError):
            numbering.validar_formato("[NumeroTurma][Sequencial]", TipoNumeracao.CODIGO_ALUNO)

    def test_nao_executa_codigo(self):
        for ruim in ("{{7*7}}[Sequencial]", "[__import__('os')][Sequencial]", "%(x)s[Sequencial]"):
            with self.assertRaises(ValidationError):
                numbering.validar_formato(ruim, TipoNumeracao.MATRICULA_OFICIAL)

    def test_previa_com_separador(self):
        ex = numbering.prever("[NumeroSerie][NumeroTurma][Sequencial]", TipoNumeracao.MATRICULA_OFICIAL, "-", 3)
        self.assertEqual(ex[0], "01-01-001")
        self.assertEqual(ex[2], "05-03-037")
