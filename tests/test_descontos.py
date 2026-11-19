"""Cadastro de descontos, aplicação na cobrança, acúmulo e dia limite."""
import itertools
import uuid
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.billing import services as bill
from apps.enrollments.forms import CobrancaForm
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin
from apps.finance.forms import DescontoForm, PlanoForm
from apps.finance.models import Desconto, Parcela

from .helpers import criar_aluno, criar_instituicao, criar_plano, criar_turma_oficial
from .test_cobranca_bancaria import Base

D = Decimal
VENC = date(2027, 1, 10)
_numero = itertools.count(2)


def novo(inst, nome, valor="10", tipo="PERCENTUAL", **kw):
    kw.setdefault("acumulativo", True)
    return Desconto.objects.create(instituicao=inst, nome=nome, tipo=tipo, valor=D(valor), **kw)


class DescontoBase(Base):
    def contratar_com(self, descontos=(), total="1000.00", parcelas=2, aluno=None):
        turma = criar_turma_oficial(self.inst, numero=next(_numero))
        plano = criar_plano(self.inst, turma, total=total, parcelas=parcelas, primeira=VENC, dia=10)
        m = criar_matricula(aluno=aluno or self.aluno, turma=turma, usuario=self.sec_user)
        return fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user,
                                   descontos=list(descontos))


class CadastroTests(DescontoBase):
    def form(self, **kw):
        dados = {"nome": "Irmãos", "tipo": "PERCENTUAL", "valor": "10", "acumulativo": "on", "ativo": "on", **kw}
        dados = {k: v for k, v in dados.items() if v is not None}
        return DescontoForm(dados, instituicao=self.inst)

    def test_plano_nao_tem_mais_campo_de_desconto(self):
        self.assertNotIn("desconto_padrao_percentual", PlanoForm(instituicao=self.inst).fields)

    def test_cria_desconto_valido(self):
        f = self.form(dia_limite="5")
        self.assertTrue(f.is_valid(), f.errors)

    def test_percentual_acima_de_100(self):
        self.assertFalse(self.form(valor="101").is_valid())
        self.assertTrue(self.form(valor="101", tipo="VALOR").is_valid())  # nominal pode ser qualquer valor

    def test_dia_limite_entre_1_e_31(self):
        for dia in ("0", "32"):
            self.assertFalse(self.form(dia_limite=dia).is_valid(), dia)

    def test_nome_repetido_na_mesma_escola(self):
        novo(self.inst, "Irmãos")
        f = self.form()
        self.assertFalse(f.is_valid())
        self.assertIn("nome", f.errors)

    def test_mesmo_nome_em_outra_escola_pode(self):
        outra = criar_instituicao(nome="Outra Escola")
        novo(outra, "Irmãos")
        self.assertTrue(self.form().is_valid())

    def test_automatico_nao_acumulativo_nao_convive_com_outros_automaticos(self):
        novo(self.inst, "Pontualidade", todos_os_planos=True)
        f = self.form(nome="Bolsa", todos_os_planos="on", acumulativo=None)
        self.assertFalse(f.is_valid())
        self.assertIn("acumulativo", f.errors)

    def test_automatico_nao_pode_entrar_se_ja_ha_automatico_nao_acumulativo(self):
        novo(self.inst, "Bolsa", todos_os_planos=True, acumulativo=False)
        f = self.form(nome="Outro", todos_os_planos="on")
        self.assertFalse(f.is_valid())
        self.assertIn("todos_os_planos", f.errors)

    def test_telas_e_permissoes(self):
        d = novo(self.inst, "Irmãos")
        c = self.logar(self.fin_user)
        self.assertContains(c.get(reverse("finance:desconto_lista")), "Irmãos")
        r = c.post(reverse("finance:desconto_novo"), {"nome": "Bolsa", "tipo": "VALOR", "valor": "50", "acumulativo": "on",
                                                      "ativo": "on", "dia_limite": "5"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Desconto.objects.filter(instituicao=self.inst, nome="Bolsa", dia_limite=5).exists())
        c.post(reverse("finance:desconto_editar", args=[d.pk]), {"nome": "Irmãos 2", "tipo": "PERCENTUAL", "valor": "15",
                                                                 "acumulativo": "on", "ativo": "on"})
        d.refresh_from_db()
        self.assertEqual((d.nome, d.valor), ("Irmãos 2", D("15")))

    def test_consulta_ve_mas_nao_cria(self):
        c = self.logar(self.cons)
        self.assertEqual(c.get(reverse("finance:desconto_lista")).status_code, 200)
        self.assertEqual(c.get(reverse("finance:desconto_novo")).status_code, 403)
        self.assertEqual(c.post(reverse("finance:desconto_novo"), {"nome": "X", "tipo": "VALOR", "valor": "1"}).status_code, 403)

    def test_secretaria_nao_cadastra(self):
        self.assertEqual(self.logar(self.sec_user).get(reverse("finance:desconto_novo")).status_code, 403)

    def test_isolamento_entre_escolas(self):
        outra = criar_instituicao(nome="Outra Escola")
        alheio = novo(outra, "Segredo")
        c = self.logar(self.fin_user)
        self.assertNotContains(c.get(reverse("finance:desconto_lista")), "Segredo")
        self.assertEqual(c.get(reverse("finance:desconto_editar", args=[alheio.pk])).status_code, 404)


class AplicacaoTests(DescontoBase):
    def test_sem_descontos_nada_muda(self):
        c = self.contratar_com()
        for p in c.parcelas.all():
            self.assertEqual((p.desconto, p.valor_devido), (D("0.00"), D("500.00")))
        self.assertFalse(c.descontos.exists())

    def test_automatico_entra_sozinho(self):
        novo(self.inst, "Pontualidade", "10", todos_os_planos=True)
        c = self.contratar_com()
        for p in c.parcelas.all():
            self.assertEqual((p.desconto, p.valor_devido), (D("50.00"), D("450.00")))
        ap = c.descontos.get()
        self.assertTrue(ap.automatico)
        self.assertEqual(ap.nome, "Pontualidade")

    def test_inativo_nao_entra_sozinho(self):
        novo(self.inst, "Velho", "10", todos_os_planos=True, ativo=False)
        self.assertEqual(self.contratar_com().parcelas.first().desconto, D("0.00"))

    def test_escolhido_na_cobranca(self):
        d = novo(self.inst, "Irmãos", "20")
        c = self.contratar_com([d])
        self.assertEqual(c.parcelas.first().desconto, D("100.00"))
        self.assertFalse(c.descontos.get().automatico)

    def test_acumulativos_somam_percentual_e_valor(self):
        auto = novo(self.inst, "Pontualidade", "10", todos_os_planos=True)
        extra = novo(self.inst, "Convênio", "30.00", tipo="VALOR")
        c = self.contratar_com([extra])
        p = c.parcelas.first()
        self.assertEqual((p.desconto, p.valor_devido), (D("80.00"), D("420.00")))  # 10% de 500 + R$ 30
        self.assertEqual(p.descontos_condicionais.count(), 2)
        self.assertEqual(c.descontos.count(), 2)
        self.assertIn(auto.nome, [a.nome for a in c.descontos.all()])

    def test_desconto_nao_passa_do_valor_da_parcela(self):
        d = novo(self.inst, "Gigante", "900.00", tipo="VALOR")
        p = self.contratar_com([d]).parcelas.first()
        self.assertEqual((p.desconto, p.valor_devido), (D("500.00"), D("0.00")))

    def test_nao_acumulativo_sozinho_pode(self):
        d = novo(self.inst, "Bolsa 50", "50", acumulativo=False)
        self.assertEqual(self.contratar_com([d]).parcelas.first().desconto, D("250.00"))

    def test_nao_acumulativo_com_outro_escolhido_e_recusado(self):
        a, b = novo(self.inst, "Bolsa 50", "50", acumulativo=False), novo(self.inst, "Irmãos", "10")
        with self.assertRaisesMessage(ValidationError, "não é acumulativo"):
            self.contratar_com([a, b])
        self.assertFalse(Parcela.objects.exists())  # nada foi gerado

    def test_nao_acumulativo_escolhido_com_automatico_e_recusado(self):
        novo(self.inst, "Pontualidade", "10", todos_os_planos=True)
        with self.assertRaisesMessage(ValidationError, "não é acumulativo"):
            self.contratar_com([novo(self.inst, "Bolsa 50", "50", acumulativo=False)])

    def test_automatico_nao_acumulativo_bloqueia_outro_escolhido(self):
        novo(self.inst, "Bolsa", "50", todos_os_planos=True, acumulativo=False)
        with self.assertRaisesMessage(ValidationError, "«Bolsa»"):
            self.contratar_com([novo(self.inst, "Irmãos", "10")])

    def test_mesmo_desconto_duas_vezes_conta_uma(self):
        d = novo(self.inst, "Irmãos", "10")
        self.assertEqual(self.contratar_com([d, d]).parcelas.first().desconto, D("50.00"))

    def test_desconto_inativo_ou_de_outra_escola_e_recusado(self):
        with self.assertRaisesMessage(ValidationError, "inativo"):
            self.contratar_com([novo(self.inst, "Velho", "10", ativo=False)])
        outra = criar_instituicao(nome="Outra Escola")
        with self.assertRaisesMessage(ValidationError, "outra instituição"):
            self.contratar_com([novo(outra, "Alheio", "10")], aluno=criar_aluno(self.inst, self.sec_user, nome="Outro Aluno"))

    def test_mudar_o_cadastro_nao_muda_contrato_existente(self):
        d = novo(self.inst, "Irmãos", "10")
        c = self.contratar_com([d])
        d.valor = D("90")
        d.save()
        self.assertEqual(c.parcelas.first().desconto, D("50.00"))
        self.assertEqual(c.descontos.get().valor, D("10.00"))

    def test_formulario_da_cobranca_valida_combinacao(self):
        a, b = novo(self.inst, "Bolsa 50", "50", acumulativo=False), novo(self.inst, "Irmãos", "10")
        turma = criar_turma_oficial(self.inst, numero=9)
        plano = criar_plano(self.inst, turma)
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        base = {"plano": plano.pk, "responsavel": self.resp.pk}
        f = CobrancaForm({**base, "descontos": [a.pk, b.pk]}, instituicao=self.inst, turma=turma, aluno=self.aluno)
        self.assertFalse(f.is_valid())
        self.assertIn("descontos", f.errors)
        f = CobrancaForm({**base, "descontos": [b.pk]}, instituicao=self.inst, turma=turma, aluno=self.aluno)
        self.assertTrue(f.is_valid(), f.errors)
        self.assertNotIn(novo(self.inst, "Auto", "5", todos_os_planos=True), f.fields["descontos"].queryset)

    def test_tela_gera_cobranca_com_desconto_escolhido(self):
        d = novo(self.inst, "Irmãos", "10")
        turma = criar_turma_oficial(self.inst, numero=9)
        plano = criar_plano(self.inst, turma, total="1000.00", parcelas=2, primeira=VENC)
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        c = self.logar(self.sec_user)
        self.assertContains(c.get(reverse("enrollments:cobrancas", args=[m.pk]) + f"?plano={plano.pk}"), "Irmãos")
        c.post(reverse("enrollments:cobrancas", args=[m.pk]), {"plano": plano.pk, "responsavel": self.resp.pk,
                                                               "descontos": [d.pk]})
        self.assertEqual(Parcela.objects.filter(matricula=m).first().desconto, D("50.00"))


class DiaLimiteTests(DescontoBase):
    def test_limite_no_mes_do_vencimento(self):
        self.assertEqual(fin.limite_do_desconto(5, date(2027, 3, 10)), date(2027, 3, 5))
        self.assertEqual(fin.limite_do_desconto(31, date(2027, 2, 10)), date(2027, 2, 28))
        self.assertIsNone(fin.limite_do_desconto(None, date(2027, 2, 10)))

    def test_cada_parcela_tem_o_limite_do_seu_mes(self):
        c = self.contratar_com([novo(self.inst, "Pontual", "10", dia_limite=5)], parcelas=2)
        limites = [p.descontos_condicionais.get().limite for p in c.parcelas.order_by("numero")]
        self.assertEqual(limites, [date(2027, 1, 5), date(2027, 2, 5)])

    def test_dia_limite_depois_do_vencimento_vale_ate_o_vencimento(self):
        c = self.contratar_com([novo(self.inst, "Tarde", "10", dia_limite=25)])
        item = c.parcelas.order_by("numero").first().descontos_condicionais.get()
        self.assertEqual(item.limite_efetivo, VENC)

    def recebe(self, p, hoje, valor=None):
        valor = valor if valor is not None else p.saldo
        return fin.registrar_recebimento(aluno=self.aluno, itens=[(p.pk, valor)], forma="PIX", valor_recebido=None,
                                         usuario=self.fin_user, token=uuid.uuid4(), hoje=hoje)

    def parcela1(self, c):
        return Parcela.objects.get(pk=c.parcelas.order_by("numero").first().pk)

    def test_ate_o_dia_limite_o_desconto_vale(self):
        p = self.parcela1(self.contratar_com([novo(self.inst, "Pontual", "10", dia_limite=5)]))
        self.recebe(p, date(2027, 1, 5), D("450.00"))
        p.refresh_from_db()
        self.assertEqual((p.status, p.desconto, p.valor_devido), ("QUITADA", D("50.00"), D("450.00")))

    def test_depois_do_dia_limite_perde_o_desconto(self):
        p = self.parcela1(self.contratar_com([novo(self.inst, "Pontual", "10", dia_limite=5)]))
        with self.assertRaisesMessage(ValidationError, "excede o saldo"):
            self.recebe(p, date(2027, 1, 6), D("500.01"))
        self.recebe(p, date(2027, 1, 6), D("450.00"))  # o caixa cobra o valor cheio: R$ 450 não quita mais
        p.refresh_from_db()
        self.assertEqual((p.status, p.desconto, p.valor_devido), ("PARCIAL", D("0.00"), D("500.00")))
        self.assertEqual(p.ajustes.filter(tipo="DESC_EXPIRADO").count(), 1)

    def test_sem_dia_limite_vale_ate_o_vencimento_e_perde_depois(self):
        c = self.contratar_com([novo(self.inst, "Sempre", "10")], parcelas=2)
        p1, p2 = [Parcela.objects.get(pk=x.pk) for x in c.parcelas.order_by("numero")]
        self.recebe(p1, VENC, D("450.00"))
        p1.refresh_from_db()
        self.assertEqual(p1.status, "QUITADA")  # no dia do vencimento ainda vale
        self.recebe(p2, date(2027, 2, 11), D("450.00"))
        p2.refresh_from_db()
        self.assertEqual((p2.status, p2.desconto), ("PARCIAL", D("0.00")))

    def test_desconto_permanente_antigo_nao_expira(self):
        p = self.parcela1(self.contratar_com())
        fin.aplicar_desconto(p, "100.00", self.fin_user, "bolsa")
        p.refresh_from_db()
        self.recebe(p, date(2027, 3, 1), D("400.00"))
        p.refresh_from_db()
        self.assertEqual((p.status, p.desconto), ("QUITADA", D("100.00")))

    def test_desconto_manual_na_parcela_substitui_os_condicionais(self):
        p = self.parcela1(self.contratar_com([novo(self.inst, "Pontual", "10", dia_limite=5)]))
        fin.aplicar_desconto(p, "80.00", self.fin_user, "acordo")
        p.refresh_from_db()
        self.assertEqual(p.descontos_condicionais.count(), 0)
        self.recebe(p, date(2027, 2, 20), D("420.00"))
        p.refresh_from_db()
        self.assertEqual(p.status, "QUITADA")

    def test_multa_e_juros_nao_levam_o_desconto_perdido_em_conta_errado(self):
        turma = criar_turma_oficial(self.inst, numero=30)
        plano = criar_plano(self.inst, turma, total="1000.00", parcelas=1, primeira=VENC, dia=10,
                            aplicar_encargos=True, multa_percentual=D("2"), juros_mensal_percentual=D("0"))
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        c = fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user,
                                descontos=[novo(self.inst, "Pontual", "10", dia_limite=5)])
        p = c.parcelas.get()
        self.assertEqual(fin.calcular_encargos(p, date(2027, 1, 20)), D("20.00"))  # 2% de 1000 (sem o desconto perdido)

    def test_caixa_mostra_o_desconto_que_vale_hoje(self):
        c = self.contratar_com([novo(self.inst, "Pontual", "10", dia_limite=5)], parcelas=1)
        p = self.parcela1(c)
        self.assertEqual(fin.desconto_vigente(p, date(2027, 1, 5)), D("100.00"))
        self.assertEqual(fin.desconto_vigente(p, date(2027, 1, 6)), D("0.00"))
        pagina = self.logar(self.fin_user).get(reverse("finance:caixa_aluno", args=[self.aluno.pk]))
        self.assertEqual(pagina.status_code, 200)


class BoletoTests(DescontoBase):
    def setUp(self):
        super().setUp()
        self.ativar()

    def parcela(self, descontos, parcelas=1):
        c = self.contratar_com(descontos, parcelas=parcelas)
        return Parcela.objects.get(pk=c.parcelas.order_by("numero").first().pk)

    def test_desconto_ate_o_vencimento_vai_no_boleto(self):
        p = self.parcela([novo(self.inst, "Sempre", "10")])
        valor, venc, _m, _j = bill._valores(p, date(2027, 1, 1))
        self.assertEqual((valor, venc), (D("900.00"), VENC))

    def test_desconto_com_dia_limite_antes_do_vencimento_sai_com_valor_cheio(self):
        p = self.parcela([novo(self.inst, "Pontual", "10", dia_limite=5)])
        valor, venc, _m, _j = bill._valores(p, date(2027, 1, 1))
        self.assertEqual((valor, venc), (D("1000.00"), VENC))

    def test_mistura_so_leva_o_que_vale_ate_o_vencimento(self):
        p = self.parcela([novo(self.inst, "Sempre", "10"), novo(self.inst, "Pontual", "5", dia_limite=5)])
        self.assertEqual(bill._valores(p, date(2027, 1, 1))[0], D("900.00"))

    def test_vencida_perde_todos_os_descontos(self):
        p = self.parcela([novo(self.inst, "Sempre", "10")])
        self.assertEqual(bill._valores(p, date(2027, 1, 20))[0], D("1000.00"))

    def test_baixa_pelo_banco_do_boleto_cheio_quita_sem_divergencia(self):
        d = novo(self.inst, "Pontual", "10", dia_limite=5)
        c = self.contratar_com([d], parcelas=1)
        p = Parcela.objects.get(pk=c.parcelas.get().pk)
        cob = bill.registrar_cobranca(p, self.adm, hoje=date(2027, 1, 1))
        self.assertEqual(cob.valor, D("1000.00"))
        cob = bill.baixar_cobranca(cob, valor_pago=D("1000.00"), data=date(2027, 1, 4), usuario=self.adm)
        p.refresh_from_db()
        self.assertEqual((p.status, p.valor_devido, p.desconto), ("QUITADA", D("1000.00"), D("0.00")))
        self.assertEqual(cob.divergencia, "")

    def test_baixa_pelo_banco_do_boleto_com_desconto_ate_o_vencimento(self):
        c = self.contratar_com([novo(self.inst, "Sempre", "10")], parcelas=1)
        p = Parcela.objects.get(pk=c.parcelas.get().pk)
        cob = bill.registrar_cobranca(p, self.adm, hoje=date(2027, 1, 1))
        self.assertEqual(cob.valor, D("900.00"))
        cob = bill.baixar_cobranca(cob, valor_pago=D("900.00"), data=VENC, usuario=self.adm)
        p.refresh_from_db()
        self.assertEqual((p.status, p.desconto), ("QUITADA", D("100.00")))
        self.assertEqual(cob.divergencia, "")


class MigracaoTests(DescontoBase):
    def test_plano_com_desconto_antigo_vira_desconto_do_catalogo(self):
        from importlib import import_module

        from django.apps import apps
        turma = criar_turma_oficial(self.inst, numero=40)
        plano = criar_plano(self.inst, turma, desconto="7.50")
        import_module("apps.finance.migrations.0007_migrar_desconto_dos_planos").migrar(apps, None)
        plano.refresh_from_db()
        self.assertEqual(plano.desconto_padrao_percentual, D("0.00"))
        d = Desconto.objects.get(instituicao=self.inst)
        self.assertEqual((d.tipo, d.valor, d.todos_os_planos, d.ativo), ("PERCENTUAL", D("7.50"), False, True))
