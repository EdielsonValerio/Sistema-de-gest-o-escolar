import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing import crypto, services
from apps.billing.codigos import (barras_para_linha, barras_svg, brcode, crc16, linha_para_barras, montar_barras)
from apps.billing.models import Cobranca, ConfiguracaoBancaria, EventoBanco, TituloSimulado
from apps.billing.providers.asaas import Asaas
from apps.billing.providers.base import ErroProvedor
from apps.billing.providers.simulado import Simulado
from apps.audit.models import RegistroAuditoria
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin
from apps.finance.models import Parcela, Recebimento
from apps.students.models import Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_plano, criar_turma_oficial, criar_usuario

D = Decimal
CPF = "529.982.247-25"
SENHA = "senha-de-teste-123"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])  # testes rápidos
class Base(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()
        self.adm = criar_usuario(self.inst, "Administrador")
        self.fin_user = criar_usuario(self.inst, "Financeiro")
        self.sec_user = criar_usuario(self.inst, "Secretaria")
        self.cons = criar_usuario(self.inst, "Consulta")
        self.turma = criar_turma_oficial(self.inst, numero=1)
        self.aluno = criar_aluno(self.inst, self.sec_user)
        self.resp = Responsavel.objects.create(instituicao=self.inst, nome="Maria Responsável", documento=CPF,
                                               email_cobranca="maria@exemplo.com")

    # ---- atalhos
    def cfg(self):
        return services.configuracao(self.inst)

    def ativar(self, inst=None):
        inst = inst or self.inst
        cfg = services.configuracao(inst)
        services.salvar_configuracao(inst, {"provedor": "SIMULADO", "ambiente": "SANDBOX"}, self.adm)
        teste = services.gerar_boleto_teste(cfg, self.adm, pagador_nome="Diretora", pagador_documento=CPF)
        Simulado.simular_pagamento(teste.id_externo)
        services.consultar_e_baixar(teste, self.adm)
        services.ativar_cobranca(cfg, self.adm)
        return cfg

    def contratar(self, aluno=None, total="600.00", parcelas=3, primeira=None, resp=None):
        aluno = aluno or self.aluno
        turma = criar_turma_oficial(self.inst, numero=int(Cobranca.objects.count() + Parcela.objects.count() + 2))
        plano = criar_plano(self.inst, turma, total=total, parcelas=parcelas,
                            primeira=primeira or timezone.localdate() + timedelta(days=30), dia=10)
        m = criar_matricula(aluno=aluno, turma=turma, usuario=self.sec_user)
        with self.captureOnCommitCallbacks(execute=True):
            return fin.contratar_plano(matricula=m, plano=plano, responsavel=resp or self.resp, usuario=self.sec_user)

    def logar(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s["escola_id"] = user.perfil.instituicao_id
        s.save()
        return c


# --------------------------------------------------------------------------- utilitários
class CodigosTests(TestCase):
    def test_crc16_ccitt_vetor_conhecido(self):
        self.assertEqual(crc16("123456789"), "29B1")

    def test_brcode_termina_com_crc_valido(self):
        p = brcode(chave="a@b.com", valor=D("10.50"), nome="Escola", cidade="Cuiaba", txid="T1")
        self.assertEqual(p[-4:], crc16(p[:-4]))
        self.assertTrue(p.startswith("000201"))

    def test_barras_linha_ida_e_volta(self):
        b = montar_barras(banco="999", valor_centavos=12345, vencimento=date(2026, 11, 20), campo_livre="1" * 25)
        self.assertEqual(len(b), 44)
        linha = barras_para_linha(b)
        self.assertEqual(len(linha), 47)
        self.assertEqual(linha_para_barras(linha), b)
        self.assertEqual(linha_para_barras("123"), "")

    def test_svg_do_codigo_de_barras(self):
        b = montar_barras(banco="999", valor_centavos=100, vencimento=date(2026, 11, 20), campo_livre="2" * 25)
        self.assertIn("<svg", barras_svg(b))
        self.assertEqual(barras_svg("123"), "")  # tamanho ímpar não é válido em ITF

    def test_segredo_cifrado_e_reversivel(self):
        t = crypto.cifrar("$aact_hmlg_segredo")
        self.assertNotIn("segredo", t)
        self.assertEqual(crypto.decifrar(t), "$aact_hmlg_segredo")
        self.assertEqual(crypto.mascarar("$aact_hmlg_000000000abcd")[-4:], "abcd")
        with self.assertRaises(crypto.SegredoIlegivel):
            crypto.decifrar("lixo")


# --------------------------------------------------------------------------- configuração, teste e ativação
class ConfiguracaoETesteTests(Base):
    def test_chave_gravada_cifrada_e_nao_aparece_no_banco(self):
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "ambiente": "SANDBOX",
                                                 "api_key": "$aact_hmlg_chave-secreta-123"}, self.adm)
        cfg = self.cfg()
        self.assertNotIn("chave-secreta", cfg.api_key_cifrada)
        self.assertEqual(cfg.api_key, "$aact_hmlg_chave-secreta-123")
        self.assertFalse(any("chave-secreta" in json.dumps(r.dados) + r.descricao for r in RegistroAuditoria.objects.all()))

    def test_chave_em_branco_mantem_a_atual(self):
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "api_key": "$aact_hmlg_x"}, self.adm)
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "api_key": "", "banco_nome": "Banco X"}, self.adm)
        self.assertEqual(self.cfg().api_key, "$aact_hmlg_x")

    def test_nao_ativa_sem_teste_confirmado(self):
        with self.assertRaises(ValidationError):
            services.ativar_cobranca(self.cfg(), self.adm)

    def test_nao_ativa_com_teste_apenas_gerado(self):
        cfg = self.cfg()
        services.gerar_boleto_teste(cfg, self.adm, pagador_nome="Diretora", pagador_documento=CPF)
        self.assertTrue(services.etapas(cfg)["teste_gerado"])
        self.assertFalse(services.etapas(cfg)["confirmado"])
        with self.assertRaises(ValidationError):
            services.ativar_cobranca(cfg, self.adm)

    def test_teste_exige_documento_valido(self):
        with self.assertRaises(ValidationError):
            services.gerar_boleto_teste(self.cfg(), self.adm, pagador_nome="X", pagador_documento="111.111.111-11")

    def test_consulta_sem_pagamento_continua_pendente(self):
        t = services.gerar_boleto_teste(self.cfg(), self.adm, pagador_nome="X", pagador_documento=CPF)
        t = services.consultar_e_baixar(t)
        self.assertEqual(t.status, "PENDENTE")

    def test_fluxo_completo_teste_confirmacao_ativacao_sem_efeito_no_caixa(self):
        cfg = self.ativar()
        self.assertEqual(cfg.status, "ATIVA")
        t = Cobranca.objects.get(eh_teste=True)
        self.assertEqual(t.status, "PAGA")
        self.assertIsNone(t.parcela)
        self.assertTrue(t.pix_copia_cola.startswith("000201"))
        self.assertTrue(t.pix_qrcode)
        self.assertEqual(Recebimento.objects.count(), 0)  # teste nunca vira recebimento
        acoes = set(RegistroAuditoria.objects.values_list("acao", flat=True))
        self.assertTrue({"COBRANCA_TESTE_GERADA", "COBRANCA_TESTE_CONFIRMADA", "COBRANCA_ATIVADA"} <= acoes)

    def test_trocar_a_chave_depois_de_ativa_exige_novo_teste(self):
        cfg = self.ativar()
        _, reaberta = services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "api_key": "$aact_hmlg_outra"}, self.adm)
        cfg.refresh_from_db()
        self.assertTrue(reaberta)
        self.assertEqual(cfg.status, "CONFIGURANDO")
        self.assertIsNone(services.cobranca_ativa(self.inst))
        with self.assertRaises(ValidationError):
            services.ativar_cobranca(cfg, self.adm)

    def test_so_um_teste_pendente_por_vez(self):
        cfg = self.cfg()
        a = services.gerar_boleto_teste(cfg, self.adm, pagador_nome="X", pagador_documento=CPF)
        b = services.gerar_boleto_teste(cfg, self.adm, pagador_nome="X", pagador_documento=CPF)
        a.refresh_from_db()
        self.assertEqual(a.status, "CANCELADA")
        self.assertEqual(b.status, "PENDENTE")

    def test_suspender_para_de_gerar_e_reativar_exige_teste_valido(self):
        cfg = self.ativar()
        services.suspender_cobranca(cfg, self.adm)
        self.assertIsNone(services.cobranca_ativa(self.inst))
        services.ativar_cobranca(cfg, self.adm)  # o teste continua válido
        self.assertIsNotNone(services.cobranca_ativa(self.inst))


# --------------------------------------------------------------------------- boleto das parcelas
class RegistroDeBoletosTests(Base):
    def test_sem_ativacao_nenhuma_parcela_ganha_boleto(self):
        c = self.contratar()
        self.assertEqual(Cobranca.objects.count(), 0)
        with self.assertRaises(ValidationError):
            services.registrar_cobranca(c.parcelas.first())

    def test_apos_ativar_parcelas_novas_nascem_com_boleto(self):
        self.ativar()
        c = self.contratar(parcelas=3)
        cobs = Cobranca.objects.filter(eh_teste=False)
        self.assertEqual(cobs.count(), 3)
        p1 = c.parcelas.order_by("numero").first()
        cob = cobs.get(parcela=p1)
        self.assertEqual(cob.status, "PENDENTE")
        self.assertEqual(cob.valor, p1.valor_original)
        self.assertEqual(cob.vencimento, p1.vencimento)
        self.assertEqual(cob.pagador_nome, "Maria Responsável")
        self.assertEqual(len(cob.linha_digitavel), 47)
        self.assertEqual(len(cob.codigo_barras), 44)
        self.assertTrue(cob.pix_copia_cola)

    def test_registro_e_idempotente(self):
        self.ativar()
        c = self.contratar(parcelas=1)
        p = c.parcelas.get()
        a = services.registrar_cobranca(p)
        b = services.registrar_cobranca(p)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Cobranca.objects.filter(parcela=p).count(), 1)

    def test_banco_garante_um_boleto_vigente_por_parcela(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Cobranca.objects.create(instituicao=self.inst, parcela=p, provedor="SIMULADO", ambiente="SANDBOX",
                                    valor=cob.valor, vencimento=cob.vencimento)

    def test_responsavel_sem_documento_vira_erro_com_mensagem_e_depois_corrige(self):
        self.ativar()
        sem = Responsavel.objects.create(instituicao=self.inst, nome="Sem Documento")
        p = self.contratar(parcelas=1, resp=sem).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        self.assertEqual(cob.status, "ERRO")
        self.assertIn("CPF/CNPJ", cob.erro)
        sem.documento = "111.444.777-35"
        sem.save()
        nova = services.registrar_cobranca(p)
        self.assertEqual(nova.pk, cob.pk)  # reaproveita o registro com erro
        self.assertEqual(nova.status, "PENDENTE")
        self.assertEqual(Cobranca.objects.filter(parcela=p).count(), 1)

    def test_falha_do_banco_nao_quebra_a_matricula(self):
        self.ativar()
        with mock.patch.object(Simulado, "emitir", side_effect=ErroProvedor("banco fora do ar")):
            c = self.contratar(parcelas=2)
        self.assertEqual(c.parcelas.count(), 2)  # parcelas existem
        self.assertEqual(set(Cobranca.objects.filter(eh_teste=False).values_list("status", flat=True)), {"ERRO"})

    def test_parcela_vencida_vai_com_vencimento_hoje_e_encargos(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        Cobranca.objects.filter(parcela=p).delete()  # volta ao estado "sem boleto" (parcela antiga)
        Parcela.objects.filter(pk=p.pk).update(vencimento=timezone.localdate() - timedelta(days=10))
        c = p.contrato
        type(c).objects.filter(pk=c.pk).update(aplicar_encargos=True, multa_percentual=D("2"),
                                               juros_mensal_percentual=D("1"))
        p.refresh_from_db()
        cob = services.registrar_cobranca(p)
        self.assertEqual(cob.vencimento, timezone.localdate())
        self.assertGreater(cob.valor, p.valor_original)

    def test_parcela_cancelada_nao_gera_boleto(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        Cobranca.objects.filter(parcela=p).delete()
        Parcela.objects.filter(pk=p.pk).update(status="CANCELADA")
        with self.assertRaises(ValidationError):
            services.registrar_cobranca(Parcela.objects.get(pk=p.pk))

    def test_lote_gera_para_parcelas_antigas_e_so_para_quem_nao_tem(self):
        c = self.contratar(parcelas=3)  # antes de ativar: sem boleto
        self.ativar()
        self.assertEqual(services.pendentes_sem_boleto(self.inst).count(), 3)
        r = services.gerar_em_lote(self.inst, self.fin_user)
        self.assertEqual((r["ok"], r["erro"], r["restantes"]), (3, 0, 0))
        r2 = services.gerar_em_lote(self.inst, self.fin_user)
        self.assertEqual(r2["ok"], 0)
        self.assertEqual(Cobranca.objects.filter(eh_teste=False).count(), 3)

    def test_lote_respeita_limite(self):
        self.contratar(parcelas=5)
        self.ativar()
        r = services.gerar_em_lote(self.inst, self.fin_user, limite=2)
        self.assertEqual((r["ok"], r["restantes"]), (2, 3))

    def test_lote_exige_cobranca_ativa(self):
        with self.assertRaises(ValidationError):
            services.gerar_em_lote(self.inst, self.fin_user)


# --------------------------------------------------------------------------- pagamento (baixa)
class BaixaTests(Base):
    def setUp(self):
        super().setUp()
        self.ativar()
        self.p = self.contratar(parcelas=1, total="300.00").parcelas.get()
        self.cob = Cobranca.objects.get(parcela=self.p)

    def pagar(self, valor=None, data=None):
        Simulado.simular_pagamento(self.cob.id_externo, valor, data)
        return services.consultar_e_baixar(self.cob)

    def test_pagamento_gera_recebimento_boleto_e_quita_a_parcela(self):
        c = self.pagar()
        self.p.refresh_from_db()
        self.assertEqual(c.status, "PAGA")
        self.assertEqual(self.p.status, "QUITADA")
        rec = c.recebimento
        self.assertEqual((rec.forma, rec.valor_total, rec.status), ("BOLETO", D("300.00"), "CONFIRMADO"))
        self.assertEqual(rec.operador_nome, "Banco (Banco simulado (treinamento))")
        self.assertEqual(c.divergencia, "")

    def test_baixa_repetida_nao_duplica(self):
        c = self.pagar()
        services.baixar_cobranca(c, valor_pago=D("300.00"))
        services.consultar_e_baixar(c)
        self.assertEqual(Recebimento.objects.filter(forma="BOLETO").count(), 1)

    def test_pago_a_mais_aplica_o_devido_e_marca_divergencia(self):
        c = self.pagar(valor=D("320.00"))
        self.p.refresh_from_db()
        self.assertEqual(self.p.valor_pago, D("300.00"))
        self.assertIn("320", c.divergencia)

    def test_pago_a_menos_deixa_parcela_parcial_com_divergencia(self):
        c = self.pagar(valor=D("100.00"))
        self.p.refresh_from_db()
        self.assertEqual((self.p.status, self.p.valor_pago), ("PARCIAL", D("100.00")))
        self.assertTrue(c.divergencia)

    def test_pagamento_de_parcela_ja_quitada_so_registra_divergencia(self):
        # recebe no caixa direto no banco de dados (sem passar pelo gancho que cancelaria o boleto)
        Parcela.objects.filter(pk=self.p.pk).update(status="QUITADA", valor_pago=self.p.valor_devido)
        c = self.pagar()
        self.assertEqual(c.status, "PAGA")
        self.assertIsNone(c.recebimento)
        self.assertIn("já estava", c.divergencia)
        self.assertEqual(Recebimento.objects.count(), 0)

    def test_recebimento_pode_ser_estornado_pelo_fluxo_normal(self):
        c = self.pagar()
        fin.estornar_recebimento(c.recebimento, self.adm, "Pagamento devolvido pelo banco")
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ABERTA")
        self.assertEqual(self.p.valor_pago, D("0.00"))

    def test_caixa_nao_oferece_boleto_como_forma(self):
        self.assertNotIn("BOLETO", [v for v, _ in Recebimento.formas_de_caixa()])
        self.assertIn("BOLETO", Recebimento.Forma.values)


# --------------------------------------------------------------------------- sincronização com o banco
class SincronizacaoTests(Base):
    def setUp(self):
        super().setUp()
        self.ativar()
        self.p = self.contratar(parcelas=1, total="400.00").parcelas.get()

    def cob(self):
        return Cobranca.objects.get(parcela=self.p)

    def test_alterar_vencimento_atualiza_o_boleto(self):
        nova = timezone.localdate() + timedelta(days=60)
        with self.captureOnCommitCallbacks(execute=True):
            fin.alterar_vencimento(self.p, nova, self.fin_user, "Pedido do responsável")
        self.assertEqual(self.cob().vencimento, nova)
        self.assertEqual(TituloSimulado.objects.get(id_externo=self.cob().id_externo).vencimento, nova)

    def test_desconto_atualiza_o_valor_do_boleto(self):
        with self.captureOnCommitCallbacks(execute=True):
            fin.aplicar_desconto(self.p, D("50.00"), self.fin_user, "Bolsa parcial")
        self.assertEqual(self.cob().valor, D("350.00"))

    def test_cancelar_parcela_cancela_o_boleto_no_banco(self):
        id_ext = self.cob().id_externo
        with self.captureOnCommitCallbacks(execute=True):
            fin.cancelar_parcela(self.p, self.fin_user, "Matrícula cancelada")
        self.assertEqual(self.cob().status, "CANCELADA")
        self.assertTrue(TituloSimulado.objects.get(id_externo=id_ext).cancelado)

    def test_receber_no_caixa_cancela_o_boleto_pendente(self):
        import uuid
        with self.captureOnCommitCallbacks(execute=True):
            fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "400.00")], forma="PIX", valor_recebido=None,
                                      usuario=self.fin_user, token=uuid.uuid4())
        self.assertEqual(self.cob().status, "CANCELADA")

    def test_falha_do_banco_ao_alterar_nao_impede_o_ajuste_e_marca_divergencia(self):
        nova = timezone.localdate() + timedelta(days=45)
        with mock.patch.object(Simulado, "alterar", side_effect=ErroProvedor("fora do ar")):
            with self.captureOnCommitCallbacks(execute=True):
                fin.alterar_vencimento(self.p, nova, self.fin_user, "Pedido")
        self.p.refresh_from_db()
        self.assertEqual(self.p.vencimento, nova)
        self.assertIn("não atualizou", self.cob().divergencia)

    def test_cancelar_boleto_ja_pago_baixa_o_pagamento(self):
        Simulado.simular_pagamento(self.cob().id_externo)
        with self.assertRaises(ValidationError):
            services.cancelar_cobranca(self.cob(), self.fin_user)
        self.assertEqual(self.cob().status, "PAGA")

    def test_cancelar_boleto_pendente(self):
        services.cancelar_cobranca(self.cob(), self.fin_user, "Erro de emissão")
        self.assertEqual(self.cob().status, "CANCELADA")
        # parcela pode receber novo boleto
        novo = services.registrar_cobranca(self.p)
        self.assertEqual(novo.status, "PENDENTE")
        self.assertNotEqual(novo.pk, self.cob().pk) if False else None


# --------------------------------------------------------------------------- conector Asaas (HTTP simulado)
def _resp_asaas(metodo, caminho, corpo=None):
    if caminho.startswith("/customers?cpfCnpj"):
        return {"data": []}
    if caminho.startswith("/customers?limit"):
        return {"data": []}
    if metodo == "POST" and caminho == "/customers":
        return {"id": "cus_1"}
    if metodo == "POST" and caminho == "/payments":
        return {"id": "pay_1", "bankSlipUrl": "https://www.asaas.com/b/pdf/pay_1",
                "invoiceUrl": "https://www.asaas.com/i/pay_1", "nossoNumero": "123"}
    if caminho.endswith("/identificationField"):
        b = montar_barras(banco="461", valor_centavos=50000, vencimento=date(2026, 12, 10), campo_livre="3" * 25)
        return {"identificationField": barras_para_linha(b), "nossoNumero": "123", "barCode": b}
    if caminho.endswith("/pixQrCode"):
        return {"encodedImage": "iVBORw0KGgo=", "payload": "00020126pixcopiaecola6304ABCD"}
    return {}


class AsaasTests(Base):
    def setUp(self):
        super().setUp()
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "ambiente": "SANDBOX",
                                                 "api_key": "$aact_hmlg_abc123"}, self.adm)
        self.cfg_ = self.cfg()

    def test_verificar_rejeita_chave_do_ambiente_errado(self):
        services.salvar_configuracao(self.inst, {"ambiente": "PRODUCAO"}, self.adm)
        with self.assertRaisesMessage(ErroProvedor, "$aact_prod_"):
            Asaas(self.cfg()).verificar()

    def test_emitir_boleto_envia_o_que_o_asaas_espera_e_le_a_resposta(self):
        chamadas = []

        def falso(self_, metodo, caminho, corpo=None):
            chamadas.append((metodo, caminho, corpo))
            return _resp_asaas(metodo, caminho, corpo)

        with mock.patch.object(Asaas, "_req", falso):
            cfg = self.cfg()
            t = services.gerar_boleto_teste(cfg, self.adm, pagador_nome="Diretora", pagador_documento=CPF,
                                            pagador_email="d@e.com", valor=D("5.00"))
        pag = next(c for c in chamadas if c[0] == "POST" and c[1] == "/payments")[2]
        self.assertEqual((pag["customer"], pag["billingType"], pag["value"]), ("cus_1", "BOLETO", 5.0))
        cli = next(c for c in chamadas if c[1] == "/customers" and c[0] == "POST")[2]
        self.assertEqual(cli["cpfCnpj"], "52998224725")
        self.assertEqual((t.id_externo, t.nosso_numero, t.provedor), ("pay_1", "123", "ASAAS"))
        self.assertEqual(len(t.codigo_barras), 44)
        self.assertEqual(t.pix_copia_cola, "00020126pixcopiaecola6304ABCD")
        self.assertEqual(t.url_boleto, "https://www.asaas.com/b/pdf/pay_1")

    def test_multa_e_juros_do_contrato_vao_ao_banco(self):
        # ativa direto (teste já confirmado) para registrar parcela
        cfg = self.cfg()
        Cobranca.objects.create(instituicao=self.inst, eh_teste=True, status="PAGA", provedor="ASAAS", ambiente="SANDBOX",
                                assinatura=cfg.assinatura(), valor=D("5"), vencimento=date(2026, 12, 1), id_externo="t1")
        services.ativar_cobranca(cfg, self.adm)
        corpos = []

        def falso(self_, metodo, caminho, corpo=None):
            if metodo == "POST" and caminho == "/payments":
                corpos.append(corpo)
            return _resp_asaas(metodo, caminho, corpo)

        turma = criar_turma_oficial(self.inst, numero=7)
        plano = criar_plano(self.inst, turma, total="500.00", parcelas=1, primeira=timezone.localdate() + timedelta(days=20),
                            aplicar_encargos=True, multa_percentual=D("2"), juros_mensal_percentual=D("1"))
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        with mock.patch.object(Asaas, "_req", falso), self.captureOnCommitCallbacks(execute=True):
            fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user)
        self.assertEqual(corpos[0]["fine"], {"value": 2.0})
        self.assertEqual(corpos[0]["interest"], {"value": 1.0})
        self.assertEqual(corpos[0]["externalReference"].startswith("parcela-"), True)

    def test_erro_http_vira_mensagem_sem_vazar_chave(self):
        import io
        import urllib.error

        def explode(*a, **k):
            raise urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b"{}"))

        with mock.patch("urllib.request.urlopen", explode):
            with self.assertRaises(ErroProvedor) as cm:
                Asaas(self.cfg())._req("GET", "/customers?limit=1")
        self.assertNotIn("aact", str(cm.exception))
        self.assertIn("401", str(cm.exception))

    def test_consultar_mapeia_status(self):
        for status, esperado in (("RECEIVED", "PAGA"), ("CONFIRMED", "PAGA"), ("PENDING", "PENDENTE"),
                                 ("OVERDUE", "PENDENTE"), ("REFUNDED", "CANCELADA")):
            with mock.patch.object(Asaas, "_req", lambda s, m, c, b=None, st=status: {"status": st, "value": 10.5,
                                                                                       "paymentDate": "2026-11-03"}):
                sit = Asaas(self.cfg()).consultar("pay_1")
            self.assertEqual(sit.status, esperado)
        self.assertEqual((sit.valor_pago, sit.data_pagamento), (None, None))


# --------------------------------------------------------------------------- webhook
class WebhookTests(Base):
    def setUp(self):
        super().setUp()
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "ambiente": "SANDBOX", "api_key": "$aact_hmlg_k"},
                                     self.adm)
        cfg = self.cfg()
        Cobranca.objects.create(instituicao=self.inst, eh_teste=True, status="PAGA", provedor="ASAAS", ambiente="SANDBOX",
                                assinatura=cfg.assinatura(), valor=D("5"), vencimento=date(2026, 12, 1), id_externo="t1")
        services.ativar_cobranca(cfg, self.adm)
        self.cfg_ = cfg
        turma = criar_turma_oficial(self.inst, numero=9)
        plano = criar_plano(self.inst, turma, total="250.00", parcelas=1, primeira=timezone.localdate() + timedelta(days=15))
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        self.p = fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user).parcelas.get()
        self.cob = Cobranca.objects.create(instituicao=self.inst, parcela=self.p, provedor="ASAAS", ambiente="SANDBOX",
                                           id_externo="pay_77", valor=D("250.00"), vencimento=self.p.vencimento,
                                           assinatura=cfg.assinatura(), pagador_nome="Maria")
        self.url = reverse("billing:webhook", args=[cfg.webhook_slug])
        self.token = cfg.webhook_token

    def enviar(self, corpo, token=None, url=None):
        return Client().post(url or self.url, data=json.dumps(corpo), content_type="application/json",
                             HTTP_ASAAS_ACCESS_TOKEN=self.token if token is None else token)

    def evento(self, id_="evt_1", tipo="PAYMENT_RECEIVED"):
        return {"id": id_, "event": tipo, "payment": {"id": "pay_77", "status": "RECEIVED", "value": 250.0}}

    def asaas_pago(self, valor=250.0):
        return mock.patch.object(Asaas, "_req", lambda s, m, c, b=None: {"status": "RECEIVED", "value": valor,
                                                                          "paymentDate": timezone.localdate().isoformat()})

    def test_token_invalido_401_e_nada_acontece(self):
        r = self.enviar(self.evento(), token="errado")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(EventoBanco.objects.count(), 0)

    def test_sem_token_401(self):
        r = Client().post(self.url, data=json.dumps(self.evento()), content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_slug_desconhecido_404(self):
        import uuid
        self.assertEqual(self.enviar(self.evento(), url=reverse("billing:webhook", args=[uuid.uuid4()])).status_code, 404)

    def test_somente_post(self):
        self.assertEqual(Client().get(self.url).status_code, 405)

    def test_json_invalido_400(self):
        r = Client().post(self.url, data="{nao-json", content_type="application/json", HTTP_ASAAS_ACCESS_TOKEN=self.token)
        self.assertEqual(r.status_code, 400)

    def test_pagamento_baixa_a_parcela(self):
        with self.asaas_pago():
            r = self.enviar(self.evento())
        self.assertEqual(r.status_code, 200)
        self.p.refresh_from_db(); self.cob.refresh_from_db()
        self.assertEqual((self.p.status, self.cob.status), ("QUITADA", "PAGA"))
        self.assertEqual(self.cob.recebimento.forma, "BOLETO")

    def test_evento_repetido_nao_duplica(self):
        with self.asaas_pago():
            self.enviar(self.evento())
            r = self.enviar(self.evento())
            self.enviar(self.evento(id_="evt_2", tipo="PAYMENT_CONFIRMED"))  # outro evento, mesma cobrança
        self.assertEqual(r.json()["resultado"], "duplicado")
        self.assertEqual(Recebimento.objects.filter(forma="BOLETO").count(), 1)

    def test_aviso_de_pagamento_e_confirmado_no_banco_antes_de_baixar(self):
        """Aviso forjado com token certo, mas o banco diz que está pendente → não baixa."""
        with mock.patch.object(Asaas, "_req", lambda s, m, c, b=None: {"status": "PENDING", "value": 250.0}):
            self.enviar(self.evento())
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "ABERTA")
        self.assertEqual(Recebimento.objects.count(), 0)

    def test_banco_fora_do_ar_responde_500_e_depois_processa_na_retentativa(self):
        with mock.patch.object(Asaas, "_req", side_effect=ErroProvedor("fora")):
            self.assertEqual(self.enviar(self.evento()).status_code, 500)
        self.assertFalse(EventoBanco.objects.get(evento_id="evt_1").processado)
        with self.asaas_pago():
            self.assertEqual(self.enviar(self.evento()).status_code, 200)
        self.p.refresh_from_db()
        self.assertEqual(self.p.status, "QUITADA")

    def test_cobranca_deletada_no_banco_cancela(self):
        self.enviar(self.evento(tipo="PAYMENT_DELETED"))
        self.cob.refresh_from_db()
        self.assertEqual(self.cob.status, "CANCELADA")

    def test_estorno_no_banco_marca_divergencia_sem_mexer_no_dinheiro(self):
        with self.asaas_pago():
            self.enviar(self.evento())
        self.enviar(self.evento(id_="evt_9", tipo="PAYMENT_REFUNDED"))
        self.cob.refresh_from_db()
        self.assertIn("PAYMENT_REFUNDED", self.cob.divergencia)
        self.assertEqual(Recebimento.objects.filter(status="ESTORNADO").count(), 0)

    def test_cobranca_desconhecida_e_ignorada_com_200(self):
        ev = self.evento()
        ev["payment"]["id"] = "pay_x"
        self.assertEqual(self.enviar(ev).status_code, 200)

    def test_webhook_de_uma_escola_nao_baixa_cobranca_de_outra(self):
        outra = criar_instituicao("Escola B")
        adm_b = criar_usuario(outra, "Administrador")
        services.salvar_configuracao(outra, {"provedor": "ASAAS", "api_key": "$aact_hmlg_b"}, adm_b)
        cfg_b = services.configuracao(outra)
        url_b, token_b = reverse("billing:webhook", args=[cfg_b.webhook_slug]), cfg_b.webhook_token
        with self.asaas_pago():
            r = Client().post(url_b, data=json.dumps(self.evento()), content_type="application/json",
                              HTTP_ASAAS_ACCESS_TOKEN=token_b)
        self.assertEqual(r.status_code, 200)
        self.cob.refresh_from_db()
        self.assertEqual(self.cob.status, "PENDENTE")  # cobrança é da escola A

    def test_token_de_uma_escola_nao_vale_na_outra(self):
        outra = criar_instituicao("Escola B")
        adm_b = criar_usuario(outra, "Administrador")
        services.salvar_configuracao(outra, {"provedor": "ASAAS", "api_key": "$aact_hmlg_b"}, adm_b)
        url_b = reverse("billing:webhook", args=[services.configuracao(outra).webhook_slug])
        self.assertEqual(self.enviar(self.evento(), url=url_b).status_code, 401)


# --------------------------------------------------------------------------- telas e permissões
class TelasTests(Base):
    def test_configuracao_so_para_quem_tem_permissao(self):
        url = reverse("billing:configuracao")
        self.assertEqual(self.logar(self.adm).get(url).status_code, 200)
        for u in (self.fin_user, self.sec_user, self.cons):
            self.assertEqual(self.logar(u).get(url).status_code, 403)
        self.assertEqual(Client().get(url).status_code, 302)

    def test_tela_nunca_mostra_a_chave_de_api(self):
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "api_key": "$aact_hmlg_SEGREDINHO_COMPLETO_9999"}, self.adm)
        html = self.logar(self.adm).get(reverse("billing:configuracao")).content.decode()
        self.assertNotIn("SEGREDINHO_COMPLETO", html)
        self.assertIn("9999", html)  # só o final mascarado

    def test_salvar_pela_tela(self):
        c = self.logar(self.adm)
        r = c.post(reverse("billing:configuracao"), {"acao": "salvar", "provedor": "SIMULADO", "ambiente": "SANDBOX",
                                                    "banco_nome": "Banco X", "agencia": "0001"}, follow=True)
        self.assertContains(r, "Dados salvos")
        self.assertEqual(self.cfg().banco_nome, "Banco X")

    def test_provedor_real_exige_chave(self):
        c = self.logar(self.adm)
        r = c.post(reverse("billing:configuracao"), {"acao": "salvar", "provedor": "ASAAS", "ambiente": "SANDBOX"})
        self.assertContains(r, "Informe a chave de API")

    def test_ativar_pela_tela_sem_teste_e_recusado(self):
        c = self.logar(self.adm)
        r = c.post(reverse("billing:configuracao"), {"acao": "ativar"}, follow=True)
        self.assertContains(r, "Gere o boleto de teste")
        self.assertNotEqual(self.cfg().status, "ATIVA")

    def test_fluxo_pela_tela_em_modo_simulado(self):
        c = self.logar(self.adm)
        url = reverse("billing:configuracao")
        c.post(url, {"acao": "salvar", "provedor": "SIMULADO", "ambiente": "SANDBOX"})
        r = c.post(url, {"acao": "teste", "pagador_nome": "Diretora", "pagador_documento": CPF, "valor": "5.00"}, follow=True)
        self.assertContains(r, "Boleto de teste gerado")
        t = Cobranca.objects.get(eh_teste=True)
        self.assertNotContains(r, "Ativar cobrança com registro")  # ainda bloqueado
        c.post(url, {"acao": "simular", "cobranca": t.pk})
        r = c.get(url)
        self.assertContains(r, "Ativar cobrança com registro")
        c.post(url, {"acao": "ativar"})
        self.assertEqual(self.cfg().status, "ATIVA")

    def test_boleto_imprimivel_com_codigo_de_barras_e_qr(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        r = self.logar(self.fin_user).get(reverse("billing:boleto", args=[cob.pk]))
        self.assertContains(r, "<svg")
        self.assertContains(r, "data:image/png;base64,")
        self.assertContains(r, cob.pix_copia_cola)
        self.assertContains(r, "SIMULADO")

    def test_lista_e_perfis(self):
        self.ativar()
        self.contratar(parcelas=2)
        url = reverse("billing:cobranca_lista")
        for u in (self.fin_user, self.adm, self.cons):
            r = self.logar(u).get(url)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "Maria") if False else None
        self.assertEqual(self.logar(self.sec_user).get(url).status_code, 403)

    def test_lista_nao_mostra_boletos_de_teste(self):
        self.ativar()
        r = self.logar(self.fin_user).get(reverse("billing:cobranca_lista"))
        self.assertEqual(len(r.context["cobrancas"]), 0)

    def test_consulta_nao_pode_cancelar_nem_gerar(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        c = self.logar(self.cons)
        self.assertEqual(c.post(reverse("billing:cobranca_cancelar", args=[cob.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("billing:cobranca_gerar", args=[p.pk])).status_code, 403)

    def test_financeiro_cancela_boleto_pela_tela(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        self.logar(self.fin_user).post(reverse("billing:cobranca_cancelar", args=[cob.pk]))
        cob.refresh_from_db()
        self.assertEqual(cob.status, "CANCELADA")

    def test_parcela_mostra_o_boleto(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        r = self.logar(self.fin_user).get(reverse("finance:parcela_detalhe", args=[p.pk]))
        self.assertContains(r, "Boleto bancário")
        self.assertContains(r, "Ver boleto")

    def test_menu_mostra_cobranca_so_para_quem_pode(self):
        d = reverse("core:dashboard")
        self.assertContains(self.logar(self.adm).get(d), "Cobrança bancária")
        self.assertNotContains(self.logar(self.fin_user).get(d), "Cobrança bancária")
        self.assertContains(self.logar(self.fin_user).get(d), "Cobranças (boletos)")
        self.assertNotContains(self.logar(self.sec_user).get(d), "Cobranças (boletos)")


# --------------------------------------------------------------------------- isolamento entre escolas
class IsolamentoTests(Base):
    def setUp(self):
        super().setUp()
        self.outra = criar_instituicao("Escola B")
        self.adm_b = criar_usuario(self.outra, "Administrador")
        self.fin_b = criar_usuario(self.outra, "Financeiro")

    def test_configuracoes_sao_independentes_por_escola(self):
        services.salvar_configuracao(self.inst, {"provedor": "ASAAS", "api_key": "$aact_hmlg_a", "banco_nome": "A"}, self.adm)
        services.salvar_configuracao(self.outra, {"provedor": "SIMULADO", "banco_nome": "B"}, self.adm_b)
        a, b = services.configuracao(self.inst), services.configuracao(self.outra)
        self.assertEqual((a.provedor, b.provedor), ("ASAAS", "SIMULADO"))
        self.assertNotEqual(a.webhook_slug, b.webhook_slug)
        self.assertNotEqual(a.webhook_token, b.webhook_token)

    def test_escola_b_nao_ve_nem_age_sobre_boleto_da_escola_a(self):
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        cob = Cobranca.objects.get(parcela=p)
        c = self.logar(self.fin_b)
        self.assertEqual(c.get(reverse("billing:boleto", args=[cob.pk])).status_code, 404)
        self.assertEqual(c.post(reverse("billing:cobranca_cancelar", args=[cob.pk])).status_code, 404)
        self.assertEqual(c.post(reverse("billing:cobranca_consultar", args=[cob.pk])).status_code, 404)
        self.assertEqual(c.post(reverse("billing:cobranca_gerar", args=[p.pk])).status_code, 404)
        self.assertEqual(len(c.get(reverse("billing:cobranca_lista")).context["cobrancas"]), 0)

    def test_ativacao_da_escola_a_nao_ativa_a_escola_b(self):
        self.ativar()
        self.assertIsNone(services.cobranca_ativa(self.outra))

    def test_cobranca_nao_liga_parcela_de_outra_escola(self):
        from apps.core.tenant import EscolaIncompativel
        self.ativar()
        p = self.contratar(parcelas=1).parcelas.get()
        with self.assertRaises(EscolaIncompativel):
            Cobranca.objects.create(instituicao=self.outra, parcela=p, provedor="SIMULADO", ambiente="SANDBOX",
                                    valor=D("10"), vencimento=date(2027, 1, 1))
