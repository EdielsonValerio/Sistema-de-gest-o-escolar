import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal
from itertools import count

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import sincronizar_perfis
from apps.audit.models import RegistroAuditoria
from apps.core.models import ConfiguracaoGeral
from apps.core.tenant import EscolaIncompativel
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin
from apps.payables import services as cp
from apps.payables.models import ContaPagar, Liquidacao
from apps.students.models import Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_plano, criar_turma_oficial, criar_usuario

D = Decimal
_n = count(1)


def dar_saldo(inst, usuario, valor):
    """Recebimento real (aluno → parcela → caixa) que aumenta o saldo da escola em `valor`."""
    turma = criar_turma_oficial(inst, numero=next(_n))
    aluno = criar_aluno(inst, usuario)
    resp = Responsavel.objects.create(instituicao=inst, nome=f"Responsável {next(_n)}")
    m = criar_matricula(aluno=aluno, turma=turma, usuario=usuario)
    c = fin.contratar_plano(matricula=m, plano=criar_plano(inst, turma, total=str(valor), parcelas=1),
                            responsavel=resp, usuario=usuario)
    p = c.parcelas.get()
    rec, _ = fin.registrar_recebimento(aluno=aluno, itens=[(p.pk, str(valor))], forma="PIX", valor_recebido=None,
                                       usuario=usuario, token=uuid.uuid4())
    return rec


def nova_conta(inst, usuario, valor="500.00", dias=10, descricao="Energia elétrica", **kw):
    dados = dict(descricao=descricao, categoria="CONSUMO", prestador="Companhia de Energia", valor=D(valor),
                 vencimento=timezone.localdate() + timedelta(days=dias))
    dados.update(kw)
    return cp.criar_contas(instituicao=inst, usuario=usuario, dados=dados)[0]


def liquidar(conta, usuario, **kw):
    kw.setdefault("data", timezone.localdate())
    kw.setdefault("forma", "PIX")
    return cp.liquidar_conta(conta, usuario=usuario, **kw)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.inst = criar_instituicao("Escola Alfa")
        cls.adm = criar_usuario(cls.inst, "Administrador")
        cls.fin = criar_usuario(cls.inst, "Financeiro")
        cls.sec = criar_usuario(cls.inst, "Secretaria")
        cls.cons = criar_usuario(cls.inst, "Consulta")

    def permitir_negativo(self, valor=True, inst=None):
        cfg = cp.configuracao(inst or self.inst)
        cfg.permitir_saldo_negativo = valor
        cfg.save()


# ----------------------------------------------------------------------------- saldo
class SaldoTests(Base):
    def test_saldo_zerado_sem_movimento(self):
        self.assertEqual(cp.saldo(self.inst), {"recebido": D("0.00"), "pago": D("0.00"), "saldo": D("0.00")})

    def test_saldo_e_recebido_menos_liquidacoes(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        liquidar(nova_conta(self.inst, self.fin, "300.00"), self.fin)
        s = cp.saldo(self.inst)
        self.assertEqual((s["recebido"], s["pago"], s["saldo"]), (D("1000.00"), D("300.00"), D("700.00")))

    def test_conta_apenas_cadastrada_nao_debita(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        nova_conta(self.inst, self.fin, "300.00")
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("1000.00"))

    def test_estorno_de_recebimento_reduz_o_saldo(self):
        rec = dar_saldo(self.inst, self.fin, "1000.00")
        fin.estornar_recebimento(rec, self.adm, "Lançamento duplicado")
        self.assertEqual(cp.saldo(self.inst)["recebido"], D("0.00"))

    def test_troco_nao_entra_no_saldo(self):
        turma = criar_turma_oficial(self.inst, numero=next(_n))
        aluno = criar_aluno(self.inst, self.fin)
        resp = Responsavel.objects.create(instituicao=self.inst, nome="Resp")
        m = criar_matricula(aluno=aluno, turma=turma, usuario=self.fin)
        c = fin.contratar_plano(matricula=m, plano=criar_plano(self.inst, turma, total="100.00", parcelas=1),
                                responsavel=resp, usuario=self.fin)
        fin.registrar_recebimento(aluno=aluno, itens=[(c.parcelas.get().pk, "100.00")], forma="DINHEIRO",
                                  valor_recebido="150.00", usuario=self.fin, token=uuid.uuid4())
        self.assertEqual(cp.saldo(self.inst)["recebido"], D("100.00"))


# ----------------------------------------------------------------------------- liquidação
class LiquidacaoTests(Base):
    def test_liquidar_debita_saldo_e_muda_situacao(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "400.00")
        liq = liquidar(c, self.fin, referencia="PIX-123")
        c.refresh_from_db()
        self.assertEqual(c.status, "LIQUIDADA")
        self.assertEqual((liq.valor, liq.forma, liq.usuario_nome), (D("400.00"), "PIX", self.fin.get_username()))
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("600.00"))

    def test_nao_permite_saldo_negativo_por_padrao(self):
        self.assertFalse(cp.configuracao(self.inst).permitir_saldo_negativo)
        dar_saldo(self.inst, self.fin, "100.00")
        c = nova_conta(self.inst, self.fin, "100.01")
        with self.assertRaisesMessage(ValidationError, "Não é permitido saldo negativo"):
            liquidar(c, self.fin)
        c.refresh_from_db()
        self.assertEqual(c.status, "ABERTA")
        self.assertFalse(Liquidacao.objects.exists())
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("100.00"))

    def test_saldo_exato_pode_zerar(self):
        dar_saldo(self.inst, self.fin, "250.00")
        liquidar(nova_conta(self.inst, self.fin, "250.00"), self.fin)
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("0.00"))

    def test_sem_nenhum_saldo_e_bloqueado(self):
        with self.assertRaisesMessage(ValidationError, "Não é permitido saldo negativo"):
            liquidar(nova_conta(self.inst, self.fin, "10.00"), self.fin)

    def test_saldo_negativo_quando_configurado(self):
        self.permitir_negativo()
        dar_saldo(self.inst, self.fin, "100.00")
        liquidar(nova_conta(self.inst, self.fin, "350.00"), self.fin)
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("-250.00"))
        reg = RegistroAuditoria.objects.get(acao="CONTA_PAGAR_LIQUIDADA")
        self.assertTrue(reg.dados["saldo_negativo"])

    def test_desligar_a_configuracao_volta_a_bloquear(self):
        self.permitir_negativo()
        liquidar(nova_conta(self.inst, self.fin, "50.00", descricao="A"), self.fin)  # saldo -50
        self.permitir_negativo(False)
        with self.assertRaisesMessage(ValidationError, "Não é permitido saldo negativo"):
            liquidar(nova_conta(self.inst, self.fin, "1.00", descricao="B"), self.fin)

    def test_valor_liquidado_pode_diferir_do_valor_da_conta(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "300.00")
        liquidar(c, self.fin, valor="315.50")  # juros
        c.refresh_from_db()
        self.assertEqual(c.valor, D("300.00"))  # valor original preservado
        self.assertEqual(cp.saldo(self.inst)["pago"], D("315.50"))

    def test_nao_liquida_duas_vezes(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        liquidar(c, self.fin)
        with self.assertRaisesMessage(ValidationError, "já foi liquidada"):
            liquidar(c, self.fin)
        self.assertEqual(Liquidacao.objects.count(), 1)

    def test_validacoes_da_liquidacao(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        with self.assertRaises(ValidationError):
            liquidar(c, self.fin, valor="0")
        with self.assertRaisesMessage(ValidationError, "não pode ser futura"):
            liquidar(c, self.fin, data=timezone.localdate() + timedelta(days=1))
        self.assertEqual(Liquidacao.objects.count(), 0)

    def test_constraint_uma_liquidacao_ativa_por_conta(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        liquidar(c, self.fin)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Liquidacao.objects.create(conta=c, valor=D("1.00"), data=date.today(), forma="PIX")

    def test_constraints_de_valor(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContaPagar.objects.create(instituicao=self.inst, numero="X-1", descricao="d", prestador="p",
                                      valor=D("0.00"), vencimento=date.today())


# ----------------------------------------------------------------------------- estorno, desativação
class SituacaoTests(Base):
    def test_estornar_liquidacao_devolve_o_saldo_e_preserva_historico(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "400.00")
        liquidar(c, self.fin)
        cp.estornar_liquidacao(c, self.adm, "Pagamento lançado em duplicidade")
        c.refresh_from_db()
        self.assertEqual(c.status, "ABERTA")
        self.assertEqual(cp.saldo(self.inst)["saldo"], D("1000.00"))
        liq = Liquidacao.objects.get()
        self.assertTrue(liq.estornada)
        self.assertEqual(liq.estorno_usuario_nome, self.adm.get_username())
        liquidar(c, self.fin)  # pode ser liquidada de novo
        self.assertEqual(Liquidacao.objects.count(), 2)
        self.assertEqual(Liquidacao.objects.filter(estornada=False).count(), 1)

    def test_estorno_exige_justificativa_e_conta_liquidada(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        with self.assertRaises(ValidationError):
            cp.estornar_liquidacao(c, self.adm, "motivo")  # conta ainda em aberto
        liquidar(c, self.fin)
        with self.assertRaises(ValidationError):
            cp.estornar_liquidacao(c, self.adm, "  ")
        c.refresh_from_db()
        self.assertEqual(c.status, "LIQUIDADA")

    def test_desativar_exige_motivo_e_nao_apaga(self):
        c = nova_conta(self.inst, self.fin)
        with self.assertRaises(ValidationError):
            cp.desativar_conta(c, self.fin, "")
        cp.desativar_conta(c, self.fin, "Serviço cancelado pelo fornecedor")
        c.refresh_from_db()
        self.assertEqual((c.status, c.desativada_por), ("DESATIVADA", self.fin))
        self.assertTrue(ContaPagar.objects.filter(pk=c.pk).exists())

    def test_conta_desativada_nao_liquida_nem_notifica_ate_reativar(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, dias=-3)
        cp.desativar_conta(c, self.fin, "Conta lançada por engano")
        with self.assertRaisesMessage(ValidationError, "desativada"):
            liquidar(c, self.fin)
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 0)
        cp.reativar_conta(c, self.fin)
        c.refresh_from_db()
        self.assertEqual((c.status, c.motivo_desativacao), ("ABERTA", ""))
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 1)
        liquidar(c, self.fin)

    def test_liquidada_nao_pode_ser_desativada_nem_editada(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        liquidar(c, self.fin)
        with self.assertRaises(ValidationError):
            cp.desativar_conta(c, self.fin, "tentativa")
        with self.assertRaises(ValidationError):
            cp.editar_conta(c, dict(descricao="x", categoria="OUTROS", prestador="p", valor=D("1.00"),
                                    vencimento=date.today()), self.fin)

    def test_editar_conta_em_aberto_audita_campos(self):
        c = nova_conta(self.inst, self.fin, "100.00")
        cp.editar_conta(c, dict(descricao=c.descricao, categoria=c.categoria, prestador=c.prestador,
                                valor=D("120.00"), vencimento=c.vencimento), self.fin)
        c.refresh_from_db()
        self.assertEqual(c.valor, D("120.00"))
        self.assertIn("valor", RegistroAuditoria.objects.get(acao="CONTA_PAGAR_ALTERADA").dados["campos"])


# ----------------------------------------------------------------------------- cadastro
class CadastroTests(Base):
    def test_numeracao_sequencial_por_ano(self):
        a, b = nova_conta(self.inst, self.fin, descricao="A"), nova_conta(self.inst, self.fin, descricao="B")
        ano = timezone.localdate().year
        self.assertEqual((a.numero, b.numero), (f"CP-{ano}-00001", f"CP-{ano}-00002"))

    def test_repeticao_mensal_gera_uma_conta_por_mes(self):
        base = date(2027, 1, 31)
        contas = cp.criar_contas(instituicao=self.inst, usuario=self.fin, repeticoes=3, dados=dict(
            descricao="Aluguel", categoria="ALUGUEL", prestador="Imobiliária", valor=D("2500.00"), vencimento=base))
        self.assertEqual([c.vencimento for c in contas], [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)])
        self.assertEqual([c.descricao for c in contas], ["Aluguel (1/3)", "Aluguel (2/3)", "Aluguel (3/3)"])
        self.assertEqual(len({c.grupo for c in contas}), 1)
        self.assertRaises(ValidationError, cp.criar_contas, instituicao=self.inst, usuario=self.fin, repeticoes=99,
                          dados=dict(descricao="x", categoria="OUTROS", prestador="p", valor=D("1"), vencimento=base))

    def test_token_evita_cadastro_duplicado(self):
        tk = uuid.uuid4()
        dados = dict(descricao="Internet", categoria="CONSUMO", prestador="Provedor", valor=D("200.00"),
                     vencimento=date(2027, 5, 5))
        a = cp.criar_contas(instituicao=self.inst, usuario=self.fin, dados=dados, token=tk)
        b = cp.criar_contas(instituicao=self.inst, usuario=self.fin, dados=dados, token=tk)
        self.assertEqual([c.pk for c in a], [c.pk for c in b])
        self.assertEqual(ContaPagar.objects.count(), 1)

    def test_valor_negativo_ou_zero_e_recusado(self):
        for v in ("0", "-5"):
            with self.assertRaises(ValidationError):
                nova_conta(self.inst, self.fin, v)
        self.assertFalse(ContaPagar.objects.exists())


# ----------------------------------------------------------------------------- notificações
class NotificacaoTests(Base):
    def test_a_vencer_dentro_do_prazo_e_vencidas(self):
        hoje = timezone.localdate()
        venc = nova_conta(self.inst, self.fin, descricao="Vencida", dias=-2)
        hoje_ = nova_conta(self.inst, self.fin, descricao="Hoje", dias=0)
        dentro = nova_conta(self.inst, self.fin, descricao="Dentro", dias=5)
        fora = nova_conta(self.inst, self.fin, descricao="Fora", dias=6)
        r = list(cp.contas_a_vencer(self.inst, hoje))
        self.assertEqual(r, [venc, hoje_, dentro])  # mais urgente primeiro
        self.assertNotIn(fora, r)

    def test_prazo_de_aviso_configuravel(self):
        nova_conta(self.inst, self.fin, descricao="Em 10 dias", dias=10)
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 0)
        cfg = cp.configuracao(self.inst)
        cfg.dias_aviso_contas_pagar = 10
        cfg.save()
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 1)

    def test_liquidada_sai_da_notificacao(self):
        self.permitir_negativo()
        c = nova_conta(self.inst, self.fin, dias=1)
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 1)
        liquidar(c, self.fin)
        self.assertEqual(cp.contas_a_vencer(self.inst).count(), 0)

    def test_textos_de_prazo(self):
        self.assertEqual(nova_conta(self.inst, self.fin, descricao="a", dias=0).prazo_texto, "Vence hoje")
        self.assertEqual(nova_conta(self.inst, self.fin, descricao="b", dias=1).prazo_texto, "Vence amanhã")
        self.assertEqual(nova_conta(self.inst, self.fin, descricao="c", dias=4).prazo_texto, "Vence em 4 dias")
        self.assertEqual(nova_conta(self.inst, self.fin, descricao="d", dias=-1).prazo_texto, "Venceu há 1 dia")

    def test_sino_e_alerta_no_dashboard_so_para_quem_ve_contas(self):
        nova_conta(self.inst, self.fin, descricao="Boleto do contador", dias=-1)
        self.client.force_login(self.fin)
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "sino-contas")
        self.assertContains(r, "Contas a pagar que precisam de atenção")
        self.assertContains(r, "Boleto do contador")
        self.client.force_login(self.sec)  # Secretaria não tem acesso ao contas a pagar
        r = self.client.get(reverse("core:dashboard"))
        self.assertNotContains(r, "sino-contas")
        self.assertNotContains(r, "Boleto do contador")

    def test_sino_sem_pendencias_mostra_mensagem(self):
        self.client.force_login(self.fin)
        r = self.client.get(reverse("core:dashboard"))
        self.assertContains(r, "Nenhuma conta vencida ou a vencer")


# ----------------------------------------------------------------------------- telas e permissões
class TelasTests(Base):
    def dados(self, **kw):
        d = dict(descricao="Manutenção do ar-condicionado", categoria="MANUTENCAO", valor="800.00",
                 vencimento=(timezone.localdate() + timedelta(days=3)).isoformat(), repetir="1",
                 prestador="Frio Ltda", prestador_documento="", numero_documento="NF 12", observacoes="",
                 token=str(uuid.uuid4()))
        d.update(kw)
        return d

    def test_matriz_de_permissoes(self):
        c = nova_conta(self.inst, self.fin)
        urls = [reverse("payables:conta_lista"), reverse("payables:conta_nova"),
                reverse("payables:conta_detalhe", args=[c.pk])]
        esperado = {"adm": (200, 200, 200), "fin": (200, 200, 200), "cons": (200, 403, 200), "sec": (403, 403, 403)}
        for quem, codigos in esperado.items():
            self.client.force_login(getattr(self, quem))
            self.assertEqual(tuple(self.client.get(u).status_code for u in urls), codigos, quem)
        self.client.logout()
        self.assertEqual(self.client.get(urls[0]).status_code, 302)

    def test_consulta_nao_executa_operacoes(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        self.client.force_login(self.cons)
        self.assertEqual(self.client.post(reverse("payables:conta_nova"), self.dados()).status_code, 403)
        for nome in ("conta_liquidar", "conta_desativar", "conta_editar", "conta_estornar", "conta_reativar"):
            self.assertEqual(self.client.post(reverse(f"payables:{nome}", args=[c.pk]), {}).status_code, 403, nome)
        c.refresh_from_db()
        self.assertEqual(c.status, "ABERTA")

    def test_financeiro_nao_estorna_liquidacao_mas_administrador_sim(self):
        dar_saldo(self.inst, self.fin, "1000.00")
        c = nova_conta(self.inst, self.fin, "100.00")
        liquidar(c, self.fin)
        url = reverse("payables:conta_estornar", args=[c.pk])
        self.client.force_login(self.fin)
        self.assertEqual(self.client.post(url, {"justificativa": "Erro de digitação"}).status_code, 403)
        self.client.force_login(self.adm)
        self.assertEqual(self.client.post(url, {"justificativa": "Erro de digitação"}).status_code, 302)
        c.refresh_from_db()
        self.assertEqual(c.status, "ABERTA")

    def test_cadastrar_pela_tela_e_enviar_duas_vezes(self):
        self.client.force_login(self.fin)
        d = self.dados()
        r = self.client.post(reverse("payables:conta_nova"), d)
        self.assertEqual(r.status_code, 302)
        self.client.post(reverse("payables:conta_nova"), d)  # reenvio
        self.assertEqual(ContaPagar.objects.count(), 1)
        c = ContaPagar.objects.get()
        self.assertEqual((c.valor, c.prestador, c.criado_por), (D("800.00"), "Frio Ltda", self.fin))
        self.assertRedirects(r, reverse("payables:conta_detalhe", args=[c.pk]))

    def test_validacoes_do_formulario(self):
        self.client.force_login(self.fin)
        for campos in ({"valor": "0"}, {"valor": "-1"}, {"descricao": ""}, {"prestador": ""},
                       {"prestador_documento": "111.111.111-11"}, {"vencimento": "1990-01-01"}):
            r = self.client.post(reverse("payables:conta_nova"), self.dados(**campos))
            self.assertEqual(r.status_code, 200, campos)
        self.assertFalse(ContaPagar.objects.exists())

    def test_liquidar_pela_tela_com_e_sem_saldo(self):
        c = nova_conta(self.inst, self.fin, "700.00")
        url = reverse("payables:conta_liquidar", args=[c.pk])
        corpo = {"data": timezone.localdate().isoformat(), "valor": "700.00", "forma": "PIX", "referencia": "",
                 "observacao": ""}
        self.client.force_login(self.fin)
        r = self.client.post(url, corpo)  # sem saldo e sem permissão de saldo negativo
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Não é permitido saldo negativo")
        c.refresh_from_db()
        self.assertEqual(c.status, "ABERTA")
        dar_saldo(self.inst, self.fin, "1000.00")
        r = self.client.post(url, corpo)
        self.assertRedirects(r, reverse("payables:conta_detalhe", args=[c.pk]))
        c.refresh_from_db()
        self.assertEqual(c.status, "LIQUIDADA")
        self.assertContains(self.client.get(r.url), "Liquidações")

    def test_configuracao_de_saldo_negativo_pela_tela(self):
        c = nova_conta(self.inst, self.fin, "50.00")
        self.client.force_login(self.adm)
        pagina = self.client.get(reverse("core:configuracoes"))
        self.assertContains(pagina, "permitir_saldo_negativo")
        self.assertContains(pagina, "dias_aviso_contas_pagar")
        r = self.client.post(reverse("core:configuracoes"), {"itens_por_pagina": "20", "permitir_saldo_negativo": "on",
                                                              "dias_aviso_contas_pagar": "7"})
        self.assertEqual(r.status_code, 302)
        cfg = cp.configuracao(self.inst)
        self.assertEqual((cfg.permitir_saldo_negativo, cfg.dias_aviso_contas_pagar), (True, 7))
        liquidar(c, self.fin)  # agora o saldo negativo é aceito
        self.client.post(reverse("core:configuracoes"), {"itens_por_pagina": "20"})  # desmarcado = proibido
        self.assertFalse(cp.configuracao(self.inst).permitir_saldo_negativo)

    def test_formulario_antigo_de_configuracoes_continua_valido(self):
        self.client.force_login(self.adm)
        r = self.client.post(reverse("core:configuracoes"), {"itens_por_pagina": "30"})
        self.assertEqual(r.status_code, 302)
        cfg = cp.configuracao(self.inst)
        self.assertEqual((cfg.itens_por_pagina, cfg.dias_aviso_contas_pagar), (30, 5))

    def test_filtros_da_lista(self):
        self.permitir_negativo()
        a = nova_conta(self.inst, self.fin, descricao="Vencida A", dias=-5)
        b = nova_conta(self.inst, self.fin, descricao="Futura B", dias=40, prestador="Papelaria Central")
        c = nova_conta(self.inst, self.fin, descricao="Liquidada C", dias=1)
        liquidar(c, self.fin)
        self.client.force_login(self.fin)
        url = reverse("payables:conta_lista")
        pega = lambda q: {x.pk for x in self.client.get(url, q).context["contas"]}  # noqa: E731
        self.assertEqual(pega({}), {a.pk, b.pk})
        self.assertEqual(pega({"situacao": "vencidas"}), {a.pk})
        self.assertEqual(pega({"situacao": "liquidadas"}), {c.pk})
        self.assertEqual(pega({"situacao": "todas"}), {a.pk, b.pk, c.pk})
        self.assertEqual(pega({"q": "papelaria"}), {b.pk})
        self.assertEqual(pega({"situacao": "todas", "categoria": "CONSUMO"}), {a.pk, b.pk, c.pk})

    def test_menu_mostra_contas_a_pagar_conforme_permissao(self):
        link = reverse("payables:conta_lista")
        self.client.force_login(self.fin)
        self.assertContains(self.client.get(reverse("core:dashboard")), link)
        self.client.force_login(self.sec)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), link)


# ----------------------------------------------------------------------------- isolamento entre escolas
class IsolamentoTests(Base):
    def test_cada_escola_tem_o_proprio_saldo_e_contas(self):
        beta = criar_instituicao("Escola Beta")
        fin_b = criar_usuario(beta, "Financeiro")
        dar_saldo(self.inst, self.fin, "1000.00")
        c_a = nova_conta(self.inst, self.fin, "100.00", descricao="Conta exclusiva da Alfa")
        self.assertEqual(cp.saldo(beta)["saldo"], D("0.00"))
        with self.assertRaisesMessage(ValidationError, "Não é permitido saldo negativo"):
            liquidar(nova_conta(beta, fin_b, "10.00"), fin_b)  # o saldo da Alfa não cobre a Beta
        self.client.force_login(fin_b)
        self.assertEqual(self.client.get(reverse("payables:conta_detalhe", args=[c_a.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("payables:conta_liquidar", args=[c_a.pk]), {}).status_code, 404)
        self.assertNotContains(self.client.get(reverse("payables:conta_lista")), c_a.descricao)
        # números são independentes por escola
        self.assertEqual(nova_conta(beta, fin_b, descricao="B1").numero.split("-")[-1], "00002")

    def test_configuracao_de_saldo_negativo_e_por_escola(self):
        beta = criar_instituicao("Escola Beta")
        self.permitir_negativo(True)
        self.assertFalse(cp.configuracao(beta).permitir_saldo_negativo)

    def test_liquidacao_herda_a_escola_da_conta(self):
        dar_saldo(self.inst, self.fin, "100.00")
        c = nova_conta(self.inst, self.fin, "10.00")
        self.assertEqual(liquidar(c, self.fin).instituicao_id, self.inst.pk)

    def test_nao_grava_liquidacao_com_escola_diferente_da_conta(self):
        beta = criar_instituicao("Escola Beta")
        c = nova_conta(self.inst, self.fin)
        with self.assertRaises(EscolaIncompativel):
            Liquidacao.objects.create(instituicao=beta, conta=c, valor=D("1.00"), data=date.today(), forma="PIX")


# ----------------------------------------------------------------------------- concorrência
class ConcorrenciaTests(TransactionTestCase):
    def test_duas_liquidacoes_simultaneas_nao_gastam_o_mesmo_saldo(self):
        inst = criar_instituicao("Escola Concorrente")
        u = User.objects.create_user("usr-conc", password="x")
        dar_saldo(inst, u, "1000.00")
        contas = [nova_conta(inst, u, "700.00", descricao=f"C{i}") for i in range(2)]
        resultados, barreira = [], threading.Barrier(2)

        def tentar(conta):
            try:
                barreira.wait(timeout=10)
                liquidar(conta, u)
                resultados.append("ok")
            except ValidationError:
                resultados.append("recusada")
            finally:
                connection.close()

        ts = [threading.Thread(target=tentar, args=(c,)) for c in contas]
        [t.start() for t in ts]
        [t.join(30) for t in ts]
        self.assertEqual(sorted(resultados), ["ok", "recusada"])
        self.assertEqual(cp.saldo(inst)["saldo"], D("300.00"))
        self.assertEqual(Liquidacao.objects.filter(estornada=False).count(), 1)
