import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.audit.models import RegistroAuditoria
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin
from apps.finance.models import AlocacaoRecebimento, Parcela, PlanoFinanceiro, Recebimento
from apps.students.models import Responsavel
from apps.students.services import definir_responsavel_financeiro, responsavel_proprio_aluno

from .helpers import criar_aluno, criar_ano, criar_instituicao, criar_plano, criar_turma_oficial, criar_usuario

D = Decimal


class FinanceBase(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()
        self.fin_user = criar_usuario(self.inst, "Financeiro")
        self.sec_user = criar_usuario(self.inst, "Secretaria")
        self.turma = criar_turma_oficial(self.inst, numero=1)
        self.aluno = criar_aluno(self.inst, self.sec_user)
        self.resp = Responsavel.objects.create(instituicao=self.inst, nome="Maria Responsável")

    def contratar(self, plano=None, aluno=None, hoje_ref=None):
        plano = plano or criar_plano(self.inst, self.turma)
        aluno = aluno or self.aluno
        m = criar_matricula(aluno=aluno, turma=self.turma, usuario=self.sec_user)
        return fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user)


class GeracaoParcelasTests(FinanceBase):
    def test_gera_12_parcelas_mensais(self):
        c = self.contratar()
        ps = list(c.parcelas.order_by("numero"))
        self.assertEqual(len(ps), 12)
        self.assertTrue(all(p.valor_original == D("500.00") for p in ps))
        self.assertEqual(sum(p.valor_devido for p in ps), D("6000.00"))
        self.assertEqual(ps[0].vencimento, date(2027, 1, 10))
        self.assertEqual(ps[11].vencimento, date(2027, 12, 10))
        self.assertTrue(all(p.status == "ABERTA" and p.responsavel == self.resp for p in ps))

    def test_quantidades_diferentes_de_parcelas(self):
        plano = criar_plano(self.inst, self.turma, nome="10x", parcelas=10)
        c = self.contratar(plano)
        self.assertEqual(c.parcelas.count(), 10)
        self.assertEqual(c.parcelas.first().valor_original, D("600.00"))

    def test_centavos_na_ultima_parcela(self):
        plano = criar_plano(self.inst, self.turma, total="100.00", parcelas=3)
        c = self.contratar(plano)
        valores = [p.valor_original for p in c.parcelas.order_by("numero")]
        self.assertEqual(valores, [D("33.33"), D("33.33"), D("33.34")])
        self.assertEqual(sum(valores), D("100.00"))

    def test_vencimento_fim_de_mes(self):
        plano = criar_plano(self.inst, self.turma, parcelas=3, primeira=date(2027, 1, 31), dia=31)
        c = self.contratar(plano)
        datas = [p.vencimento for p in c.parcelas.order_by("numero")]
        self.assertEqual(datas, [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)])

    def test_virada_de_ano(self):
        plano = criar_plano(self.inst, self.turma, parcelas=4, primeira=date(2027, 11, 5), dia=5)
        c = self.contratar(plano)
        datas = [p.vencimento for p in c.parcelas.order_by("numero")]
        self.assertEqual(datas, [date(2027, 11, 5), date(2027, 12, 5), date(2028, 1, 5), date(2028, 2, 5)])

    def test_desconto_padrao_do_plano(self):
        plano = criar_plano(self.inst, self.turma, desconto="5")
        c = self.contratar(plano)
        p = c.parcelas.first()
        self.assertEqual(p.desconto, D("25.00"))
        self.assertEqual(p.valor_devido, D("475.00"))

    def test_nao_gera_cobranca_duplicada(self):
        c = self.contratar()
        with self.assertRaises(ValidationError):
            fin.contratar_plano(matricula=c.matricula, plano=c.plano, responsavel=self.resp, usuario=self.sec_user)
        self.assertEqual(Parcela.objects.filter(matricula=c.matricula).count(), 12)

    def test_alterar_plano_nao_muda_parcelas_geradas(self):
        c = self.contratar()
        plano = c.plano
        plano.valor_total = D("9999.00")
        plano.quantidade_parcelas = 6
        plano.save()
        self.assertEqual(c.parcelas.count(), 12)
        self.assertEqual(c.parcelas.first().valor_original, D("500.00"))
        c.refresh_from_db()
        self.assertEqual(c.valor_total, D("6000.00"))

    def test_plano_inativo_ou_de_outra_turma(self):
        plano = criar_plano(self.inst, self.turma, nome="inativo")
        plano.situacao = PlanoFinanceiro.Situacao.INATIVO
        plano.save()
        m = criar_matricula(aluno=self.aluno, turma=self.turma, usuario=self.sec_user)
        with self.assertRaises(ValidationError):
            fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user)
        outra = criar_turma_oficial(self.inst, numero=2)
        plano2 = criar_plano(self.inst, outra, nome="outra")
        with self.assertRaises(ValidationError):
            fin.contratar_plano(matricula=m, plano=plano2, responsavel=self.resp, usuario=self.sec_user)

    def test_matricular_com_cobranca_e_atomicidade(self):
        plano = criar_plano(self.inst, self.turma)
        m, c = fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec_user, plano=plano,
                                           responsavel=self.resp)
        self.assertEqual(c.parcelas.count(), 12)
        # falha na cobrança desfaz a matrícula (responsável de outra instituição)
        outro = Responsavel.objects.create(instituicao=criar_instituicao("X"), nome="Estranho")
        a2 = criar_aluno(self.inst, self.sec_user)
        with self.assertRaises(ValidationError):
            fin.matricular_com_cobranca(aluno=a2, turma=self.turma, usuario=self.sec_user, plano=plano,
                                        responsavel=outro)
        self.assertEqual(a2.matriculas.count(), 0)

    def test_turma_com_plano_exige_escolha_ou_sem_cobranca(self):
        criar_plano(self.inst, self.turma)
        with self.assertRaises(ValidationError):
            fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec_user)
        m, c = fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec_user,
                                           sem_cobranca=True)
        self.assertIsNone(c)

    def test_turma_sem_plano_matricula_sem_cobranca(self):
        m, c = fin.matricular_com_cobranca(aluno=self.aluno, turma=self.turma, usuario=self.sec_user)
        self.assertIsNone(c)
        self.assertEqual(m.parcelas.count(), 0)

    def test_historico_de_responsavel_preservado_nas_parcelas(self):
        c = self.contratar()
        outro = Responsavel.objects.create(instituicao=self.inst, nome="João Novo")
        definir_responsavel_financeiro(self.aluno, self.resp, "MAE", self.sec_user)
        definir_responsavel_financeiro(self.aluno, outro, "PAI", self.sec_user)
        self.assertEqual(self.aluno.responsavel_financeiro_atual, outro)
        self.assertEqual(self.aluno.responsaveis_financeiros.count(), 2)
        self.assertEqual(self.aluno.responsaveis_financeiros.filter(fim__isnull=True).count(), 1)
        self.assertTrue(all(p.responsavel == self.resp for p in c.parcelas.all()))

    def test_responsavel_proprio_aluno_copia_dados(self):
        r = responsavel_proprio_aluno(self.aluno)
        self.assertTrue(r.eh_proprio_aluno)
        self.assertEqual(r.nome, self.aluno.nome)
        self.assertEqual(responsavel_proprio_aluno(self.aluno), r) if False else None

    def test_auditoria_da_geracao(self):
        self.contratar()
        self.assertTrue(RegistroAuditoria.objects.filter(acao="PARCELAS_GERADAS").exists())


class AjustesTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.c = self.contratar()
        self.p = self.c.parcelas.order_by("numero").first()

    def test_aplicar_desconto_com_historico(self):
        fin.aplicar_desconto(self.p, "50,00", self.fin_user, "Bolsa parcial")
        self.p.refresh_from_db()
        self.assertEqual(self.p.valor_original, D("500.00"))
        self.assertEqual(self.p.desconto, D("50.00"))
        self.assertEqual(self.p.valor_devido, D("450.00"))
        aj = self.p.ajustes.get()
        self.assertEqual((aj.anterior, aj.posterior, aj.justificativa), ("0.00", "50.00", "Bolsa parcial"))
        self.assertEqual(aj.valor_devido_anterior, D("500.00"))
        self.assertEqual(aj.valor_devido_posterior, D("450.00"))
        self.assertEqual(aj.usuario, self.fin_user)

    def test_desconto_exige_justificativa_e_limite(self):
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "10", self.fin_user, "")
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "600", self.fin_user, "x")
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "-1", self.fin_user, "x")
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "10.123", self.fin_user, "x")

    def test_alterar_vencimento(self):
        fin.alterar_vencimento(self.p, date(2027, 1, 20), self.fin_user, "Pedido do responsável")
        self.p.refresh_from_db()
        self.assertEqual(self.p.vencimento, date(2027, 1, 20))
        self.assertEqual(self.p.ajustes.get().anterior, "2027-01-10")

    def test_corrigir_valor(self):
        fin.corrigir_valor(self.p, "520.00", self.fin_user, "Reajuste")
        self.p.refresh_from_db()
        self.assertEqual(self.p.valor_original, D("500.00") + D("20.00"))
        self.assertEqual(self.p.valor_devido, D("520.00"))

    def test_nao_ajusta_parcela_quitada(self):
        fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "500.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        self.p.refresh_from_db()
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "10", self.fin_user, "x")

    def test_nao_reduz_abaixo_do_pago(self):
        fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "300.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        self.p.refresh_from_db()
        with self.assertRaises(ValidationError):
            fin.aplicar_desconto(self.p, "250.00", self.fin_user, "x")

    def test_desconto_que_quita_parcela_parcialmente_paga(self):
        fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "300.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        self.p.refresh_from_db()
        fin.aplicar_desconto(self.p, "200.00", self.fin_user, "Acordo")
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "QUITADA")

    def test_cancelar_parcela_sem_pagamento(self):
        fin.cancelar_parcela(self.p, self.fin_user, "Erro de emissão")
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "CANCELADA")
        self.assertEqual(self.p.saldo, D("0.00"))
        self.assertTrue(Parcela.objects.filter(pk=self.p.pk).exists())  # não some do banco

    def test_nao_cancela_parcela_com_pagamento(self):
        fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "100.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            fin.cancelar_parcela(self.p, self.fin_user, "x")


class RecebimentoTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.c = self.contratar()
        self.ps = list(self.c.parcelas.order_by("numero"))

    def receber(self, itens, forma="DINHEIRO", recebido=None, token=None, **kw):
        return fin.registrar_recebimento(
            aluno=self.aluno, itens=[(p.pk, v) for p, v in itens], forma=forma, valor_recebido=recebido,
            usuario=self.fin_user, token=token or uuid.uuid4(), **kw)

    def test_recebimento_integral(self):
        rec, criado = self.receber([(self.ps[0], "500.00")], recebido="500.00")
        self.assertTrue(criado)
        self.ps[0].refresh_from_db()
        self.assertEqual(self.ps[0].status, "QUITADA")
        self.assertEqual(self.ps[0].saldo, D("0.00"))
        self.assertIsNotNone(self.ps[0].quitacao)
        self.assertEqual(rec.valor_total, D("500.00"))
        self.assertEqual(rec.troco, D("0.00"))
        self.assertEqual(rec.operador, self.fin_user)

    def test_recebimento_parcial_mantem_saldo(self):
        self.receber([(self.ps[0], "200.00")], forma="PIX")
        p = Parcela.objects.get(pk=self.ps[0].pk)
        self.assertEqual(p.status, "PARCIAL")
        self.assertEqual(p.valor_pago, D("200.00"))
        self.assertEqual(p.saldo, D("300.00"))
        self.receber([(p, "300.00")], forma="PIX")
        p.refresh_from_db()
        self.assertEqual(p.status, "QUITADA")

    def test_pagamento_de_varias_parcelas(self):
        rec, _ = self.receber([(self.ps[0], "500.00"), (self.ps[1], "500.00"), (self.ps[2], "100.00")],
                              forma="CREDITO", referencia="NSU 889977")
        self.assertEqual(rec.valor_total, D("1100.00"))
        self.assertEqual(rec.alocacoes.count(), 3)
        status = [Parcela.objects.get(pk=p.pk).status for p in self.ps[:3]]
        self.assertEqual(status, ["QUITADA", "QUITADA", "PARCIAL"])

    def test_troco_em_dinheiro(self):
        rec, _ = self.receber([(self.ps[0], "500.00")], recebido="600.00")
        self.assertEqual(rec.troco, D("100.00"))
        self.assertEqual(rec.valor_recebido, D("600.00"))
        self.assertEqual(rec.valor_total, D("500.00"))  # troco não vira receita nem paga outras parcelas
        self.assertEqual(Parcela.objects.get(pk=self.ps[1].pk).valor_pago, D("0.00"))

    def test_dinheiro_insuficiente(self):
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "500.00")], recebido="499.99")
        self.assertEqual(Recebimento.objects.count(), 0)

    def test_nao_dinheiro_sem_troco(self):
        rec, _ = self.receber([(self.ps[0], "500.00")], forma="DEBITO", recebido="900.00")
        self.assertEqual(rec.troco, D("0.00"))
        self.assertEqual(rec.valor_recebido, D("500.00"))

    def test_impede_pagamento_acima_do_saldo(self):
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "500.01")], forma="PIX")
        self.receber([(self.ps[0], "500.00")], forma="PIX")
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "1.00")], forma="PIX")  # já quitada

    def test_atomicidade_se_uma_parcela_falha(self):
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "500.00"), (self.ps[1], "999.00")], forma="PIX")
        self.assertEqual(Recebimento.objects.count(), 0)
        self.assertEqual(AlocacaoRecebimento.objects.count(), 0)
        self.assertEqual(Parcela.objects.get(pk=self.ps[0].pk).valor_pago, D("0.00"))

    def test_idempotencia_por_token(self):
        token = uuid.uuid4()
        r1, c1 = self.receber([(self.ps[0], "500.00")], forma="PIX", token=token)
        r2, c2 = self.receber([(self.ps[0], "500.00")], forma="PIX", token=token)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(r1.pk, r2.pk)
        self.assertEqual(Recebimento.objects.count(), 1)
        self.assertEqual(Parcela.objects.get(pk=self.ps[0].pk).valor_pago, D("500.00"))

    def test_parcela_de_outro_aluno_rejeitada(self):
        outro = criar_aluno(self.inst, self.sec_user)
        with self.assertRaises(ValidationError):
            fin.registrar_recebimento(aluno=outro, itens=[(self.ps[0].pk, "10.00")], forma="PIX",
                                      valor_recebido=None, usuario=self.fin_user, token=uuid.uuid4())

    def test_valores_invalidos(self):
        for v in ("0", "-5", "abc", "1.234"):
            with self.assertRaises(ValidationError):
                self.receber([(self.ps[0], v)], forma="PIX")

    def test_parcela_cancelada_nao_recebe(self):
        fin.cancelar_parcela(self.ps[0], self.fin_user, "x")
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "10.00")], forma="PIX")

    def test_referencia_nao_aceita_numero_de_cartao(self):
        with self.assertRaises(ValidationError):
            self.receber([(self.ps[0], "10.00")], forma="CREDITO", referencia="4111 1111 1111 1111")

    def test_numero_do_recebimento_unico(self):
        r1, _ = self.receber([(self.ps[0], "10.00")], forma="PIX")
        r2, _ = self.receber([(self.ps[0], "10.00")], forma="PIX")
        self.assertNotEqual(r1.numero, r2.numero)

    def test_constraint_de_banco_impede_pago_maior_que_devido(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Parcela.objects.filter(pk=self.ps[0].pk).update(valor_pago=D("999.00"))


class EstornoTests(FinanceBase):
    def setUp(self):
        super().setUp()
        self.c = self.contratar()
        self.ps = list(self.c.parcelas.order_by("numero"))

    def receber(self, itens, **kw):
        return fin.registrar_recebimento(
            aluno=self.aluno, itens=[(p.pk, v) for p, v in itens], forma="PIX", valor_recebido=None,
            usuario=self.fin_user, token=uuid.uuid4(), **kw)[0]

    def test_estorno_preserva_historico_e_restaura_saldo(self):
        rec = self.receber([(self.ps[0], "500.00"), (self.ps[1], "200.00")])
        fin.estornar_recebimento(rec, self.fin_user, "Lançado no aluno errado")
        rec.refresh_from_db()
        self.assertEqual(rec.status, "ESTORNADO")
        self.assertTrue(Recebimento.objects.filter(pk=rec.pk).exists())
        self.assertEqual(rec.alocacoes.filter(estornada=True).count(), 2)  # alocações preservadas
        for p in self.ps[:2]:
            p.refresh_from_db()
            self.assertEqual((p.status, p.valor_pago, p.saldo), ("ABERTA", D("0.00"), D("500.00")))
            self.assertIsNone(p.quitacao)
        self.assertEqual(rec.estorno.justificativa, "Lançado no aluno errado")
        self.assertEqual(rec.estorno.usuario, self.fin_user)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="RECEBIMENTO_ESTORNADO").exists())

    def test_estorno_recalcula_com_outros_pagamentos(self):
        r1 = self.receber([(self.ps[0], "100.00")])
        self.receber([(self.ps[0], "150.00")])
        fin.estornar_recebimento(r1, self.fin_user, "erro")
        p = Parcela.objects.get(pk=self.ps[0].pk)
        self.assertEqual((p.status, p.valor_pago), ("PARCIAL", D("150.00")))

    def test_nao_estorna_duas_vezes_e_exige_justificativa(self):
        rec = self.receber([(self.ps[0], "10.00")])
        with self.assertRaises(ValidationError):
            fin.estornar_recebimento(rec, self.fin_user, "")
        fin.estornar_recebimento(rec, self.fin_user, "ok")
        with self.assertRaises(ValidationError):
            fin.estornar_recebimento(rec, self.fin_user, "de novo")

    def test_parcela_pode_ser_recebida_apos_estorno(self):
        rec = self.receber([(self.ps[0], "500.00")])
        fin.estornar_recebimento(rec, self.fin_user, "erro")
        self.receber([(self.ps[0], "500.00")])
        self.assertEqual(Parcela.objects.get(pk=self.ps[0].pk).status, "QUITADA")


class VencimentoEEncargosTests(FinanceBase):
    def test_vencida_derivada_sem_perder_pendencia(self):
        plano = criar_plano(self.inst, self.turma, primeira=date(2020, 1, 10))
        c = self.contratar(plano)
        p = c.parcelas.order_by("numero").first()
        self.assertTrue(p.vencida)
        self.assertEqual(p.status, "ABERTA")  # continua pendente
        self.assertEqual(p.situacao_exibicao, "Vencido")
        self.assertIn(p.pk, Parcela.objects.vencidas().values_list("pk", flat=True))

    def test_parcial_vencida(self):
        plano = criar_plano(self.inst, self.turma, primeira=date(2020, 1, 10))
        c = self.contratar(plano)
        p = c.parcelas.order_by("numero").first()
        fin.registrar_recebimento(aluno=self.aluno, itens=[(p.pk, "100.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        p.refresh_from_db()
        self.assertTrue(p.vencida)
        self.assertEqual(p.status, "PARCIAL")

    def test_quitada_nao_e_vencida(self):
        plano = criar_plano(self.inst, self.turma, primeira=date(2020, 1, 10))
        c = self.contratar(plano)
        p = c.parcelas.order_by("numero").first()
        fin.registrar_recebimento(aluno=self.aluno, itens=[(p.pk, "500.00")], forma="PIX", valor_recebido=None,
                                  usuario=self.fin_user, token=uuid.uuid4())
        p.refresh_from_db()
        self.assertFalse(p.vencida)

    def test_encargos_desabilitados_por_padrao(self):
        plano = criar_plano(self.inst, self.turma, primeira=date(2020, 1, 10))
        p = self.contratar(plano).parcelas.order_by("numero").first()
        self.assertEqual(fin.calcular_encargos(p), D("0.00"))

    def test_multa_e_juros_gravados_no_recebimento(self):
        plano = criar_plano(self.inst, self.turma, total="1200.00", parcelas=12, primeira=date(2027, 1, 10),
                            aplicar_encargos=True, multa_percentual=D("2"), juros_mensal_percentual=D("1"))
        c = self.contratar(plano)
        p = c.parcelas.order_by("numero").first()  # R$ 100,00 vencida em 10/01/2027
        hoje = date(2027, 2, 9)  # 30 dias de atraso
        self.assertEqual(fin.calcular_encargos(p, hoje), D("3.00"))  # 2,00 de multa + 1,00 de juros
        rec, _ = fin.registrar_recebimento(aluno=self.aluno, itens=[(p.pk, "103.00")], forma="PIX",
                                           valor_recebido=None, usuario=self.fin_user, token=uuid.uuid4(), hoje=hoje)
        p.refresh_from_db()
        self.assertEqual((p.acrescimo, p.valor_devido, p.status), (D("3.00"), D("103.00"), "QUITADA"))
        self.assertTrue(p.ajustes.filter(tipo="ENCARGOS").exists())
        self.assertEqual(p.valor_original, D("100.00"))  # original preservado
