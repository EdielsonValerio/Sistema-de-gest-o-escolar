"""Integração Cora (Integração Direta): conector, credenciais, tela e webhook. Nenhuma chamada real: o HTTP é simulado."""
import datetime as dt
import functools
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.billing import crypto, services
from apps.billing.forms import ConfiguracaoBancariaForm
from apps.billing.models import Cobranca, EventoBanco
from apps.billing.providers import provedor_da
from apps.billing.providers.base import ErroProvedor, Pagador, Titulo
from apps.billing.providers.cora import Cora, _contexto_ssl, validar_credencial
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin

from .helpers import criar_plano, criar_turma_oficial
from .test_cobranca_bancaria import CPF, D, Base


def par_pem(dias=365, outra_chave=False):
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "teste")])
    agora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - dt.timedelta(days=400))
            .not_valid_after(agora + dt.timedelta(days=dias)).sign(chave, hashes.SHA256()))
    if outra_chave:
        chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode())


CERT, CHAVE = par_pem()


def fatura(id_="inv_1", status="OPEN", total=25000, pix="00020126cora6304ABCD", **extra):
    base = {"id": id_, "status": status, "total_amount": total, "total_paid": total if status == "PAID" else 0,
            "code": "parcela-1", "customer": {"name": "Maria", "document": {"identity": "52998224725", "type": "CPF"}},
            "services": [{"name": "Mensalidade", "amount": total}], "payment_terms": {"due_date": "2027-01-10"},
            "payment_options": {"bank_slip": {"barcode": "00191" + "1" * 39, "digitable": "00191.11111 11111.111111",
                                              "our_number": "999", "url": "https://cora/x.pdf", "registered": True}},
            "pix": {"emv": pix}}
    base.update(extra)
    return base


class FalsoCora:
    """Substitui Cora._chamar: registra as chamadas e responde por (método, caminho)."""

    def __init__(self, respostas=None):
        self.respostas, self.chamadas = respostas or {}, []

    def __call__(self, cora, metodo, caminho, corpo=None, *, token=None, idem=None, form=None):
        self.chamadas.append(dict(metodo=metodo, caminho=caminho, corpo=corpo, token=token, idem=idem, form=form))
        if caminho == "/token":
            return {"access_token": "tok-secreto", "expires_in": 86400, "token_type": "Bearer"}
        r = self.respostas.get((metodo, caminho), self.respostas.get(metodo))
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(corpo)
        return r if r is not None else {}

    def __get__(self, obj, tipo=None):  # age como método ao ser colocado na classe pelo mock
        return functools.partial(self.__call__, obj)

    def de(self, metodo, caminho=None):
        return [c for c in self.chamadas if c["metodo"] == metodo and (caminho is None or c["caminho"].startswith(caminho))]


class CoraBase(Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        services.salvar_configuracao(self.inst, {
            "provedor": "CORA", "ambiente": "SANDBOX", "client_id": "int-abc123", "certificado_pem": CERT,
            "chave_privada_pem": CHAVE, "certificado_validade": date.today() + timedelta(days=300),
            "certificado_digital": "ab" * 32}, self.adm)

    def titulo(self, **kw):
        base = dict(referencia="parcela-1", valor=D("250.00"), vencimento=date(2027, 1, 10), descricao="Mensalidade 01/2027",
                    pagador=Pagador(nome="Maria Responsável", documento="52998224725", email="maria@exemplo.com"),
                    idempotencia="1:0")
        base.update(kw)
        return Titulo(**base)

    def usar(self, falso):
        return mock.patch.object(Cora, "_chamar", falso)

    def ativar_cora(self):
        cfg = self.cfg()
        Cobranca.objects.create(instituicao=self.inst, eh_teste=True, status="PAGA", provedor="CORA", ambiente="SANDBOX",
                                assinatura=cfg.assinatura(), valor=D("5"), vencimento=date(2027, 1, 1), id_externo="inv_t")
        services.ativar_cobranca(cfg, self.adm)
        return cfg


class CredenciaisTests(CoraBase):
    def test_segredos_ficam_cifrados_no_banco(self):
        cfg = self.cfg()
        for campo in (cfg.certificado_cifrado, cfg.chave_privada_cifrada):
            self.assertNotIn("BEGIN", campo)
        self.assertEqual(crypto.decifrar(cfg.chave_privada_cifrada).strip(), CHAVE.strip())
        self.assertTrue(cfg.completa)

    def test_sem_client_id_ou_certificado_nao_esta_completa(self):
        cfg = self.cfg()
        cfg.client_id = ""
        self.assertFalse(cfg.completa)

    def test_assinatura_muda_com_o_certificado(self):
        a = self.cfg().assinatura()
        services.salvar_configuracao(self.inst, {"certificado_pem": CERT, "chave_privada_pem": CHAVE,
                                                 "certificado_validade": date.today(), "certificado_digital": "cd" * 32},
                                     self.adm)
        self.assertNotEqual(a, self.cfg().assinatura())

    def test_validar_credencial_ok(self):
        validade, digital = validar_credencial(CERT, CHAVE)
        self.assertGreater(validade, date.today())
        self.assertEqual(len(digital), 64)

    def test_chave_que_nao_corresponde(self):
        cert, chave = par_pem(outra_chave=True)
        with self.assertRaisesMessage(ValueError, "não corresponde"):
            validar_credencial(cert, chave)

    def test_certificado_vencido(self):
        cert, chave = par_pem(dias=-5)
        with self.assertRaisesMessage(ValueError, "venceu"):
            validar_credencial(cert, chave)

    def test_arquivo_que_nao_e_pem(self):
        with self.assertRaises(ValueError):
            validar_credencial("lixo", CHAVE)
        with self.assertRaises(ValueError):
            validar_credencial(CERT, "lixo")

    def test_contexto_ssl_carrega_o_par_e_nao_deixa_arquivo(self):
        import os
        import tempfile
        antes = set(os.listdir(tempfile.gettempdir()))
        _contexto_ssl(CERT, CHAVE)
        self.assertEqual(set(os.listdir(tempfile.gettempdir())), antes)
        with self.assertRaisesMessage(ErroProvedor, "inválidos"):
            _contexto_ssl(CERT, "nada")

    def test_rotacao_de_segredos_recifra_certificado_e_chave(self):
        from apps.billing.management.commands import rotacionar_segredos as m
        self.assertIn("certificado_cifrado", open(m.__file__).read())


class FormTests(CoraBase):
    def form(self, cert=CERT, chave=CHAVE, **extra):
        dados = {"provedor": "CORA", "ambiente": "SANDBOX", "client_id": "int-xyz", **extra}
        arquivos = {}
        if cert is not None:
            arquivos["certificado_arquivo"] = SimpleUploadedFile("certificate.pem", cert.encode())
        if chave is not None:
            arquivos["chave_arquivo"] = SimpleUploadedFile("private-key.key", chave.encode())
        return ConfiguracaoBancariaForm(dados, arquivos, cfg=self.cfg(), instituicao=self.inst)

    def test_upload_valido(self):
        f = self.form()
        self.assertTrue(f.is_valid(), f.errors)
        self.assertEqual(f.cleaned_data["certificado_pem"], CERT)

    def test_so_um_dos_arquivos(self):
        f = self.form(chave=None)
        self.assertFalse(f.is_valid())
        self.assertIn("chave_arquivo", f.errors)

    def test_par_que_nao_combina(self):
        cert, chave = par_pem(outra_chave=True)
        f = self.form(cert=cert, chave=chave)
        self.assertFalse(f.is_valid())
        self.assertIn("certificado_arquivo", f.errors)

    def test_primeira_vez_exige_arquivos_mas_depois_mantem(self):
        self.assertTrue(self.form(cert=None, chave=None).is_valid())  # já há certificado gravado
        from .helpers import criar_instituicao
        nova = criar_instituicao(nome="Escola Nova")
        f = ConfiguracaoBancariaForm({"provedor": "CORA", "ambiente": "SANDBOX", "client_id": "x"},
                                     cfg=services.configuracao(nova), instituicao=nova)
        self.assertFalse(f.is_valid())
        self.assertIn("certificado_arquivo", f.errors)

    def test_arquivo_gigante_e_recusado(self):
        f = self.form(cert="x" * 30000)
        self.assertFalse(f.is_valid())

    def test_tela_nunca_mostra_os_segredos(self):
        c = self.logar(self.adm)
        html = c.get(reverse("billing:configuracao")).content.decode()
        self.assertNotIn("BEGIN", html)
        self.assertNotIn("PRIVATE KEY", html)
        self.assertIn("int-abc123", html)
        self.assertIn("válido até", html)


class ConectorTests(CoraBase):
    def test_token_mtls_e_cache(self):
        f = FalsoCora({"GET": {"items": []}})
        with self.usar(f):
            p = provedor_da(self.cfg())
            p.verificar()
            p._api("GET", "/v2/invoices/inv_1")
        tok = f.de("POST", "/token")[0]
        self.assertEqual(tok["form"], {"grant_type": "client_credentials", "client_id": "int-abc123"})
        self.assertEqual(f.de("GET", "/v2/invoices/inv_1")[0]["token"], "tok-secreto")
        # o token fica cifrado no cache
        guardado = cache.get(f"cora-token:{self.cfg().pk}:{self.cfg().assinatura()[:24]}")
        self.assertNotIn("tok-secreto", guardado)

    def test_emitir_envia_o_que_o_cora_espera(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(total=25000)})
        with self.usar(f):
            e = provedor_da(self.cfg()).emitir(self.titulo(multa_percentual=D("2"), juros_mensal_percentual=D("1")))
        envio = f.de("POST", "/v2/invoices/")[0]
        c = envio["corpo"]
        self.assertEqual(c["services"][0]["amount"], 25000)
        self.assertEqual(c["payment_terms"], {"due_date": "2027-01-10", "fine": {"rate": 2.0}, "interest": {"rate": 1.0}})
        self.assertEqual(c["payment_forms"], ["BANK_SLIP", "PIX"])
        self.assertEqual(c["customer"]["document"], {"identity": "52998224725", "type": "CPF"})
        self.assertEqual(c["customer"]["email"], "maria@exemplo.com")
        self.assertEqual(c["code"], "parcela-1")
        self.assertTrue(envio["idem"])
        self.assertEqual((e.id_externo, e.nosso_numero, e.url_boleto), ("inv_1", "999", "https://cora/x.pdf"))
        self.assertEqual(len(e.codigo_barras), 44)
        self.assertEqual(e.pix_copia_cola, "00020126cora6304ABCD")
        self.assertTrue(e.pix_qrcode)

    def test_chave_de_idempotencia_estavel_e_distinta_por_tentativa(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura()})
        with self.usar(f):
            p = provedor_da(self.cfg())
            p.emitir(self.titulo())
            p.emitir(self.titulo())
            p.emitir(self.titulo(idempotencia="1:1"))
        ks = [c["idem"] for c in f.de("POST", "/v2/invoices/")]
        self.assertEqual(ks[0], ks[1])
        self.assertNotEqual(ks[0], ks[2])

    def test_sem_pix_na_emissao_consulta_de_novo(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(pix=""), ("GET", "/v2/invoices/inv_1"): fatura()})
        with self.usar(f):
            e = provedor_da(self.cfg()).emitir(self.titulo())
        self.assertEqual(e.pix_copia_cola, "00020126cora6304ABCD")

    def test_conta_sem_chave_pix_emite_so_o_boleto(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(pix=""), ("GET", "/v2/invoices/inv_1"): fatura(pix="")})
        with self.usar(f):
            e = provedor_da(self.cfg()).emitir(self.titulo())
        self.assertEqual((e.pix_copia_cola, e.pix_qrcode), ("", ""))
        self.assertEqual(e.id_externo, "inv_1")

    def test_valor_minimo(self):
        with self.usar(FalsoCora()), self.assertRaisesMessage(ErroProvedor, "R$ 5,00"):
            provedor_da(self.cfg()).emitir(self.titulo(valor=D("4.99")))

    def test_nome_curto(self):
        with self.usar(FalsoCora()), self.assertRaisesMessage(ErroProvedor, "nome do pagador"):
            provedor_da(self.cfg()).emitir(self.titulo(pagador=Pagador(nome="Al", documento="52998224725")))

    def test_consultar_mapeia_situacoes(self):
        casos = {"OPEN": "PENDENTE", "LATE": "PENDENTE", "IN_PAYMENT": "PENDENTE", "CANCELLED": "CANCELADA"}
        for status, esperado in casos.items():
            with self.usar(FalsoCora({"GET": fatura(status=status)})):
                self.assertEqual(provedor_da(self.cfg()).consultar("inv_1").status, esperado, status)
        pago = fatura(status="PAID", total=25100, occurrence_date="2027-01-09T10:00:00Z")
        with self.usar(FalsoCora({"GET": pago})):
            s = provedor_da(self.cfg()).consultar("inv_1")
        self.assertEqual((s.status, s.valor_pago, s.data_pagamento), ("PAGA", D("251.00"), date(2027, 1, 9)))

    def test_cancelar_e_boleto_ja_pago(self):
        f = FalsoCora({"DELETE": {}})
        with self.usar(f):
            provedor_da(self.cfg()).cancelar("inv_1")
        self.assertEqual(len(f.de("DELETE", "/v2/invoices/inv_1")), 1)
        with self.usar(FalsoCora({"DELETE": ErroProvedor("Cora recusou a operação (422 REC-0006): x")})), \
                self.assertRaisesMessage(ErroProvedor, "já foi pago"):
            provedor_da(self.cfg()).cancelar("inv_1")

    def test_alterar_emite_novo_e_cancela_o_antigo(self):
        f = FalsoCora({("GET", "/v2/invoices/inv_1"): fatura(),
                       ("POST", "/v2/invoices/"): fatura(id_="inv_2", total=30000), "DELETE": {}})
        with self.usar(f):
            novo = provedor_da(self.cfg()).alterar("inv_1", valor=D("300.00"), vencimento=date(2027, 2, 10))
        corpo = f.de("POST", "/v2/invoices/")[0]["corpo"]
        self.assertEqual((corpo["services"][0]["amount"], corpo["payment_terms"]["due_date"]), (30000, "2027-02-10"))
        self.assertEqual(novo.id_externo, "inv_2")
        self.assertEqual(novo.aviso, "")
        self.assertEqual(f.de("DELETE")[0]["caminho"], "/v2/invoices/inv_1")
        ordem = [c["metodo"] for c in f.chamadas if c["caminho"] != "/token"]
        self.assertLess(ordem.index("POST"), ordem.index("DELETE"))  # só cancela depois de emitir o novo

    def test_alterar_falha_ao_cancelar_o_antigo_avisa(self):
        f = FalsoCora({("GET", "/v2/invoices/inv_1"): fatura(), ("POST", "/v2/invoices/"): fatura(id_="inv_2"),
                       "DELETE": ErroProvedor("fora do ar")})
        with self.usar(f):
            novo = provedor_da(self.cfg()).alterar("inv_1", valor=D("300.00"), vencimento=date(2027, 2, 10))
        self.assertEqual(novo.id_externo, "inv_2")
        self.assertIn("inv_1", novo.aviso)
        self.assertIn("duplicidade", novo.aviso)

    def test_alterar_boleto_pago_e_recusado(self):
        f = FalsoCora({("GET", "/v2/invoices/inv_1"): fatura(status="PAID")})
        with self.usar(f), self.assertRaisesMessage(ErroProvedor, "já foi pago"):
            provedor_da(self.cfg()).alterar("inv_1", valor=D("300.00"), vencimento=date(2027, 2, 10))
        self.assertEqual(f.de("POST", "/v2/invoices/"), [])

    def test_simular_pagamento_so_no_teste(self):
        f = FalsoCora()
        with self.usar(f):
            provedor_da(self.cfg()).simular_pagamento("inv_1")
        self.assertEqual(f.de("POST", "/v2/invoices/pay")[0]["corpo"], {"id": "inv_1"})
        services.salvar_configuracao(self.inst, {"ambiente": "PRODUCAO"}, self.adm)
        with self.usar(FalsoCora()), self.assertRaisesMessage(ErroProvedor, "ambiente de teste"):
            provedor_da(self.cfg()).simular_pagamento("inv_1")

    def test_registrar_webhook(self):
        f = FalsoCora()
        with self.usar(f):
            provedor_da(self.cfg()).registrar_webhook("https://x.com/w/?t=abc")
        c = f.de("POST", "/endpoints/")[0]
        self.assertEqual((c["corpo"]["url"], c["corpo"]["resource"], c["corpo"]["trigger"]), ("https://x.com/w/?t=abc", "invoice", "*"))

    def test_erro_http_nao_vaza_credenciais(self):
        import urllib.error
        exc = urllib.error.HTTPError("https://x", 401, "no", {}, io.BytesIO(b"{}"))
        msg = Cora._mensagem_http(exc)
        self.assertIn("401", msg)
        self.assertNotIn("int-abc123", msg)

    def test_token_vencido_renova_e_repete_uma_vez(self):
        chamadas = {"n": 0}

        def f(cora, metodo, caminho, corpo=None, *, token=None, idem=None, form=None):
            if caminho == "/token":
                return {"access_token": f"t{chamadas['n']}", "expires_in": 86400}
            chamadas["n"] += 1
            if chamadas["n"] == 1:
                raise ErroProvedor("Cora recusou as credenciais (401).")
            return {"ok": True}

        with mock.patch.object(Cora, "_chamar", f):
            self.assertEqual(provedor_da(self.cfg())._api("GET", "/v2/invoices/inv_1"), {"ok": True})


class ParcelaTests(CoraBase):
    def setUp(self):
        super().setUp()
        self.ativar_cora()
        turma = criar_turma_oficial(self.inst, numero=9)
        plano = criar_plano(self.inst, turma, total="250.00", parcelas=1, primeira=timezone.localdate() + timedelta(days=20))
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        self.p = fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user).parcelas.get()

    def test_registrar_cobranca_da_parcela(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(id_="inv_9")})
        with self.usar(f):
            cob = services.registrar_cobranca(self.p, self.adm)
        self.assertEqual((cob.status, cob.id_externo, cob.provedor), ("PENDENTE", "inv_9", "CORA"))
        self.assertTrue(cob.pix_qrcode)
        self.assertEqual(f.de("POST", "/v2/invoices/")[0]["corpo"]["code"], f"parcela-{self.p.pk}")

    def test_alterar_parcela_troca_o_id_do_boleto(self):
        f = FalsoCora({("POST", "/v2/invoices/"): lambda c: fatura(id_="inv_" + str(c["services"][0]["amount"])),
                       ("GET", "/v2/invoices/inv_9"): fatura(id_="inv_9"), "DELETE": {}})
        f.respostas[("POST", "/v2/invoices/")] = fatura(id_="inv_9")
        with self.usar(f):
            cob = services.registrar_cobranca(self.p, self.adm)
        self.p.desconto = D("50.00")
        self.p.save()
        f.respostas[("POST", "/v2/invoices/")] = fatura(id_="inv_10", total=20000)
        with self.usar(f):
            cob = services.sincronizar_parcela(self.p, self.adm)
        cob.refresh_from_db()
        self.assertEqual((cob.id_externo, cob.valor), ("inv_10", D("200.00")))
        self.assertEqual(f.de("DELETE")[0]["caminho"], "/v2/invoices/inv_9")

    def test_aviso_de_boleto_antigo_nao_cancelado_vira_divergencia(self):
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(id_="inv_9")})
        with self.usar(f):
            services.registrar_cobranca(self.p, self.adm)
        self.p.desconto = D("50.00")
        self.p.save()
        f = FalsoCora({("GET", "/v2/invoices/inv_9"): fatura(id_="inv_9"),
                       ("POST", "/v2/invoices/"): fatura(id_="inv_10", total=20000), "DELETE": ErroProvedor("fora")})
        with self.usar(f):
            cob = services.sincronizar_parcela(self.p, self.adm)
        cob.refresh_from_db()
        self.assertIn("duplicidade", cob.divergencia)
        self.assertEqual(cob.id_externo, "inv_10")


class CaixaCoraTests(ParcelaTests):
    def test_receber_no_caixa_cancela_o_boleto_no_cora(self):
        import uuid
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(id_="inv_9"), "DELETE": {}})
        with self.usar(f):
            cob = services.registrar_cobranca(self.p, self.adm)
            with self.captureOnCommitCallbacks(execute=True):
                fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "250.00")], forma="DINHEIRO",
                                          valor_recebido="250.00", usuario=self.adm, token=uuid.uuid4())
        cob.refresh_from_db()
        self.assertEqual(cob.status, "CANCELADA")
        self.assertEqual(f.de("DELETE")[0]["caminho"], "/v2/invoices/inv_9")

    def test_cora_recusa_cancelar_e_a_escola_e_avisada(self):
        import uuid
        f = FalsoCora({("POST", "/v2/invoices/"): fatura(id_="inv_9"),
                       "DELETE": ErroProvedor("Cora recusou a operação (422 REC-0006): já pago")})
        with self.usar(f):
            cob = services.registrar_cobranca(self.p, self.adm)
            with self.captureOnCommitCallbacks(execute=True):
                fin.registrar_recebimento(aluno=self.aluno, itens=[(self.p.pk, "250.00")], forma="DINHEIRO",
                                          valor_recebido="250.00", usuario=self.adm, token=uuid.uuid4())
        cob.refresh_from_db()
        self.assertEqual(cob.status, "PENDENTE")
        self.assertIn("não cancelou o boleto", cob.divergencia)


class WebhookCoraTests(CoraBase):
    def setUp(self):
        super().setUp()
        self.ativar_cora()
        turma = criar_turma_oficial(self.inst, numero=9)
        plano = criar_plano(self.inst, turma, total="250.00", parcelas=1, primeira=timezone.localdate() + timedelta(days=15))
        m = criar_matricula(aluno=self.aluno, turma=turma, usuario=self.sec_user)
        self.p = fin.contratar_plano(matricula=m, plano=plano, responsavel=self.resp, usuario=self.sec_user).parcelas.get()
        self.cob = Cobranca.objects.create(instituicao=self.inst, parcela=self.p, provedor="CORA", ambiente="SANDBOX",
                                           id_externo="inv_77", valor=D("250.00"), vencimento=self.p.vencimento,
                                           assinatura=self.cfg().assinatura(), pagador_nome="Maria")
        cfg = self.cfg()
        self.url = reverse("billing:webhook", args=[cfg.webhook_slug])
        self.token = cfg.webhook_token

    def enviar(self, evento="evt_1", tipo="invoice.PAID", boleto="inv_77", token=None, url=None):
        h = {}
        if evento is not None:
            h["HTTP_WEBHOOK_EVENT_ID"] = evento
        if tipo is not None:
            h["HTTP_WEBHOOK_EVENT_TYPE"] = tipo
        if boleto is not None:
            h["HTTP_WEBHOOK_RESOURCE_ID"] = boleto
        t = self.token if token is None else token
        return Client().post((url or self.url) + (f"?t={t}" if t else ""), **h)

    def pago(self, valor=25000):
        return mock.patch.object(Cora, "_chamar", FalsoCora({"GET": fatura(id_="inv_77", status="PAID", total=valor,
                                                                          occurrence_date=timezone.localdate().isoformat())}))

    def test_pagamento_baixa_a_parcela(self):
        with self.pago():
            r = self.enviar()
        self.assertEqual(r.status_code, 200)
        self.cob.refresh_from_db()
        self.p.refresh_from_db()
        self.assertEqual(self.cob.status, "PAGA")
        self.assertEqual(self.p.status, "QUITADA")

    def test_sem_corpo_e_sem_confiar_no_aviso_se_o_banco_nao_confirma(self):
        with mock.patch.object(Cora, "_chamar", FalsoCora({"GET": fatura(id_="inv_77", status="OPEN")})):
            r = self.enviar()
        self.assertEqual(r.status_code, 200)
        self.cob.refresh_from_db()
        self.assertEqual(self.cob.status, "PENDENTE")  # aviso falso não dá baixa

    def test_token_errado_ou_ausente(self):
        for t in ("errado", None):
            h = Client().post(self.url + (f"?t={t}" if t else ""), HTTP_WEBHOOK_EVENT_ID="evt_1",
                              HTTP_WEBHOOK_EVENT_TYPE="invoice.PAID", HTTP_WEBHOOK_RESOURCE_ID="inv_77")
            self.assertEqual(h.status_code, 401)
        self.assertEqual(EventoBanco.objects.count(), 0)

    def test_cabecalhos_invalidos(self):
        for kw in ({"evento": None}, {"boleto": None}, {"tipo": None}, {"evento": "x; drop"}, {"boleto": "../inv_1"},
                   {"tipo": "invoice.PAID\nX"}, {"boleto": "pay_77"}):
            self.assertEqual(self.enviar(**kw).status_code, 400, kw)

    def test_evento_repetido_e_idempotente(self):
        with self.pago():
            self.enviar()
            r = self.enviar()
        self.assertEqual(r.json()["resultado"], "duplicado")
        self.assertEqual(self.p.recebimentos.count() if hasattr(self.p, "recebimentos") else 1, 1)
        self.assertEqual(EventoBanco.objects.count(), 1)

    def test_boleto_desconhecido_responde_200(self):
        r = self.enviar(boleto="inv_naoexiste")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["resultado"], "cobrança desconhecida")

    def test_falha_ao_consultar_pede_nova_tentativa(self):
        with mock.patch.object(Cora, "_chamar", FalsoCora({"GET": ErroProvedor("fora do ar")})):
            r = self.enviar()
        self.assertEqual(r.status_code, 500)
        self.assertFalse(EventoBanco.objects.get().processado)  # o reenvio do Cora é processado depois
        with self.pago():
            self.assertEqual(self.enviar().status_code, 200)

    def test_so_aceita_post(self):
        self.assertEqual(Client().get(self.url + f"?t={self.token}").status_code, 405)

    def test_webhook_de_outra_escola_nao_mexe_na_cobranca(self):
        from .helpers import criar_instituicao
        outra = criar_instituicao(nome="Outra Escola")
        services.salvar_configuracao(outra, {"provedor": "CORA", "ambiente": "SANDBOX", "client_id": "x",
                                             "certificado_pem": CERT, "chave_privada_pem": CHAVE,
                                             "certificado_validade": date.today(), "certificado_digital": "ee" * 32}, self.adm)
        cfg2 = services.configuracao(outra)
        with self.pago():
            r = self.enviar(url=reverse("billing:webhook", args=[cfg2.webhook_slug]), token=cfg2.webhook_token)
        self.assertEqual(r.json()["resultado"], "cobrança desconhecida")
        self.cob.refresh_from_db()
        self.assertEqual(self.cob.status, "PENDENTE")


class TelaCoraTests(CoraBase):
    def test_botoes_do_cora_na_tela(self):
        c = self.logar(self.adm)
        html = c.get(reverse("billing:configuracao")).content.decode()
        self.assertIn("Cadastrar aviso no Cora", html)
        self.assertIn("multipart/form-data", html)

    def test_cadastrar_webhook_pela_tela(self):
        c = self.logar(self.adm)
        f = FalsoCora()
        with self.usar(f):
            c.post(reverse("billing:configuracao"), {"acao": "webhook_cora"})
        env = f.de("POST", "/endpoints/")[0]["corpo"]
        self.assertIn(self.cfg().webhook_token, env["url"])

    def test_simular_pagamento_do_teste_no_sandbox(self):
        c = self.logar(self.adm)
        cfg = self.cfg()
        teste = Cobranca.objects.create(instituicao=self.inst, eh_teste=True, provedor="CORA", ambiente="SANDBOX",
                                        assinatura=cfg.assinatura(), valor=D("5"), vencimento=date(2027, 1, 1),
                                        id_externo="inv_t")
        f = FalsoCora({"GET": fatura(id_="inv_t", status="PAID", total=500, occurrence_date=date.today().isoformat())})
        with self.usar(f):
            c.post(reverse("billing:configuracao"), {"acao": "simular", "cobranca": teste.pk})
        teste.refresh_from_db()
        self.assertEqual(teste.status, "PAGA")
        self.assertEqual(len(f.de("POST", "/v2/invoices/pay")), 1)

    def test_testar_conexao(self):
        c = self.logar(self.adm)
        f = FalsoCora({"GET": {"items": []}})
        with self.usar(f):
            c.post(reverse("billing:configuracao"), {"acao": "conexao"})
        self.assertEqual(len(f.de("GET", "/v2/invoices/")), 1)
