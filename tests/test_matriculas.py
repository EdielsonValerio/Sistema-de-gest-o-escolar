import threading
from datetime import date

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from apps.core import numbering
from apps.core.models import ConfiguracaoNumeracao, EscopoNumeracao, TipoNumeracao
from apps.enrollments.models import Matricula
from apps.enrollments.services import (ativar_matricula, cancelar_matricula, concluir_matricula, criar_matricula,
                                       transferir_matricula)

from .helpers import (criar_aluno, criar_ano, criar_instituicao, criar_turma_nao_oficial, criar_turma_oficial,
                      criar_usuario)


class MatriculaTests(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()
        self.user = criar_usuario(self.inst, "Secretaria")
        self.ano = criar_ano(self.inst)
        self.turma_a = criar_turma_oficial(self.inst, self.ano, numero=1, nome="1º Ano A")
        self.turma_b = criar_turma_oficial(self.inst, self.ano, numero=2, nome="1º Ano B")
        self.aluno = criar_aluno(self.inst, self.user)

    def test_matricula_oficial_valida_gera_numero(self):
        m = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        self.assertEqual(m.status, Matricula.Status.ATIVA)
        self.assertEqual(m.ano_letivo, self.ano)
        # [AnoLetivo][NumeroSerie][NumeroTurma][Sequencial] com sequencial de 3 dígitos
        self.assertEqual(m.numero, "2027" + "01" + "01" + "001")

    def test_sequencial_por_turma(self):
        m1 = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        m2 = criar_matricula(aluno=criar_aluno(self.inst, self.user), turma=self.turma_a, usuario=self.user)
        m3 = criar_matricula(aluno=criar_aluno(self.inst, self.user), turma=self.turma_b, usuario=self.user)
        self.assertTrue(m1.numero.endswith("001"))
        self.assertTrue(m2.numero.endswith("002"))
        self.assertTrue(m3.numero.endswith("001"))
        self.assertNotEqual(m1.numero, m3.numero)

    def test_impede_segunda_matricula_oficial_no_ano(self):
        criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        with self.assertRaises(ValidationError):
            criar_matricula(aluno=self.aluno, turma=self.turma_b, usuario=self.user)
        self.assertEqual(Matricula.objects.filter(aluno=self.aluno).count(), 1)

    def test_outro_ano_letivo_permitido(self):
        criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        outra = criar_turma_oficial(self.inst, criar_ano(self.inst, 2028), numero=1)
        m = criar_matricula(aluno=self.aluno, turma=outra, usuario=self.user)
        self.assertEqual(m.ano_letivo.ano, 2028)

    def test_varias_turmas_nao_oficiais_sem_afetar_oficial(self):
        oficial = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        n1 = criar_turma_nao_oficial(self.inst, numero=1, nome="Reforço")
        n2 = criar_turma_nao_oficial(self.inst, numero=2, nome="Oficina")
        criar_matricula(aluno=self.aluno, turma=n1, usuario=self.user, data_ingresso=date(2027, 3, 5))
        criar_matricula(aluno=self.aluno, turma=n2, usuario=self.user, data_ingresso=date(2027, 3, 5))
        oficial.refresh_from_db()
        self.assertEqual(oficial.status, Matricula.Status.ATIVA)
        self.assertEqual(self.aluno.matriculas.filter(tipo="NAO_OFICIAL").count(), 2)

    def test_nao_oficial_respeita_periodo(self):
        n = criar_turma_nao_oficial(self.inst)
        with self.assertRaises(ValidationError):
            criar_matricula(aluno=self.aluno, turma=n, usuario=self.user, data_ingresso=date(2027, 8, 1))

    def test_nao_oficial_nao_gera_inscricao_por_padrao(self):
        n = criar_turma_nao_oficial(self.inst)
        m = criar_matricula(aluno=self.aluno, turma=n, usuario=self.user, data_ingresso=date(2027, 3, 5))
        self.assertIsNone(m.numero)

    def test_inscricao_quando_habilitada(self):
        cfg = numbering.obter_configuracao(self.inst, TipoNumeracao.INSCRICAO_NAO_OFICIAL)
        cfg.ativa = True
        cfg.save()
        n = criar_turma_nao_oficial(self.inst)
        m = criar_matricula(aluno=self.aluno, turma=n, usuario=self.user, data_ingresso=date(2027, 3, 5))
        self.assertEqual(m.numero, "2027" + "01" + "001")

    def test_transferencia_preserva_historico(self):
        m1 = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        m2 = transferir_matricula(m1, self.turma_b, self.user, "Pedido da família")
        m1.refresh_from_db()
        self.assertEqual(m1.status, Matricula.Status.TRANSFERIDA)
        self.assertIsNotNone(m1.data_saida)
        self.assertEqual(m1.numero, "2027" + "01" + "01" + "001")  # número antigo intacto
        self.assertEqual(m2.status, Matricula.Status.ATIVA)
        self.assertEqual(m2.matricula_anterior_id, m1.pk)
        self.assertEqual(m2.turma, self.turma_b)
        self.assertEqual(Matricula.objects.filter(aluno=self.aluno).count(), 2)

    def test_transferencia_exige_justificativa_e_mesmo_ano(self):
        m1 = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        with self.assertRaises(ValidationError):
            transferir_matricula(m1, self.turma_b, self.user, "  ")
        outra = criar_turma_oficial(self.inst, criar_ano(self.inst, 2028), numero=1)
        with self.assertRaises(ValidationError):
            transferir_matricula(m1, outra, self.user, "teste")
        m1.refresh_from_db()
        self.assertEqual(m1.status, Matricula.Status.ATIVA)

    def test_transicoes_invalidas(self):
        m = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        cancelar_matricula(m, self.user, "Desistência")
        for fn in (lambda: ativar_matricula(m, self.user), lambda: concluir_matricula(m, self.user)):
            with self.assertRaises(ValidationError):
                fn()

    def test_pendente_ativa_e_conclui(self):
        m = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user, pendente=True)
        self.assertEqual(m.status, Matricula.Status.PENDENTE)
        ativar_matricula(m, self.user)
        m.refresh_from_db()
        concluir_matricula(m, self.user)
        m.refresh_from_db()
        self.assertEqual(m.status, Matricula.Status.CONCLUIDA)

    def test_capacidade_maxima(self):
        t = criar_turma_oficial(self.inst, self.ano, numero=3, nome="Pequena", capacidade=1)
        criar_matricula(aluno=self.aluno, turma=t, usuario=self.user)
        with self.assertRaises(ValidationError):
            criar_matricula(aluno=criar_aluno(self.inst, self.user), turma=t, usuario=self.user)

    def test_outra_instituicao_rejeitada(self):
        outra = criar_instituicao("Outra")
        t = criar_turma_oficial(outra, numero=1)
        with self.assertRaises(ValidationError):
            criar_matricula(aluno=self.aluno, turma=t, usuario=self.user)

    def test_alterar_configuracao_nao_renumera_historico(self):
        m1 = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        antigo = m1.numero
        cfg = numbering.obter_configuracao(self.inst, TipoNumeracao.MATRICULA_OFICIAL)
        cfg.formato = "M[AnoLetivo][NumeroTurma][Sequencial]"
        cfg.separador = "-"
        cfg.save()
        m1.refresh_from_db()
        self.assertEqual(m1.numero, antigo)
        m2 = criar_matricula(aluno=criar_aluno(self.inst, self.user), turma=self.turma_a, usuario=self.user)
        self.assertEqual(m2.numero, "M2027-01-002")

    def test_escopo_instituicao_compartilha_sequencia(self):
        cfg = numbering.obter_configuracao(self.inst, TipoNumeracao.MATRICULA_OFICIAL)
        cfg.formato, cfg.escopo = "[AnoLetivo][NumeroTurma][Sequencial]", EscopoNumeracao.INSTITUICAO
        cfg.save()
        m1 = criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        m2 = criar_matricula(aluno=criar_aluno(self.inst, self.user), turma=self.turma_b, usuario=self.user)
        self.assertTrue(m1.numero.endswith("001"))
        self.assertTrue(m2.numero.endswith("002"))

    def test_consulta_todos_os_numeros_do_aluno(self):
        criar_matricula(aluno=self.aluno, turma=self.turma_a, usuario=self.user)
        n = criar_turma_nao_oficial(self.inst)
        cfg = numbering.obter_configuracao(self.inst, TipoNumeracao.INSCRICAO_NAO_OFICIAL)
        cfg.ativa = True
        cfg.save()
        criar_matricula(aluno=self.aluno, turma=n, usuario=self.user, data_ingresso=date(2027, 3, 5))
        numeros = list(self.aluno.matriculas.exclude(numero=None).values_list("numero", flat=True))
        self.assertEqual(len(numeros), 2)


class ConcorrenciaTests(TransactionTestCase):
    """Várias threads gerando números ao mesmo tempo: nenhuma duplicidade."""

    def _executar(self, alvo, n=8):
        erros, resultados = [], []

        def run():
            try:
                resultados.append(alvo())
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)
            finally:
                connection.close()

        ts = [threading.Thread(target=run) for _ in range(n)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return resultados, erros

    def test_codigo_aluno_sem_duplicidade(self):
        inst = criar_instituicao()
        resultados, erros = self._executar(lambda: numbering.gerar_codigo_aluno(inst, 2027))
        self.assertEqual(erros, [])
        self.assertEqual(len(resultados), len(set(resultados)), resultados)
        self.assertEqual(sorted(resultados), [f"2027{i:06d}" for i in range(1, 9)])

    def test_matricula_sem_duplicidade(self):
        inst = criar_instituicao()
        user = criar_usuario(inst, "Secretaria")
        turma = criar_turma_oficial(inst, numero=1)
        alunos = [criar_aluno(inst, user) for _ in range(6)]
        it = iter(alunos)
        lock = threading.Lock()

        def matricular():
            with lock:
                a = next(it)
            return criar_matricula(aluno=a, turma=turma, usuario=user).numero

        resultados, erros = self._executar(matricular, n=6)
        self.assertEqual(erros, [])
        self.assertEqual(len(set(resultados)), 6)
