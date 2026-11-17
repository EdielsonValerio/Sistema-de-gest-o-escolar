"""Comunicados por WhatsApp: normalização de telefone, config cifrada, destinatários (turmas/escola/um), envio em lotes,
isolamento entre escolas e permissões."""
import io
import json
import urllib.error
from unittest import mock

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.audit.models import RegistroAuditoria
from apps.comunicacao import services as sv
from apps.comunicacao import whatsapp as wa
from apps.comunicacao.models import Comunicado, ConfiguracaoWhatsApp, EnvioComunicado
from apps.enrollments.models import Matricula
from apps.enrollments.services import criar_matricula
from apps.students.models import AlunoResponsavelFinanceiro, Responsavel

from .helpers import criar_aluno, criar_ano, criar_instituicao, criar_turma_oficial, criar_usuario

TOKEN = "EAAG-token-secreto-1234567890"
HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class TelefoneTests(TestCase):
    def test_normalizacao(self):
        self.assertEqual(wa.normalizar_telefone("(65) 99999-1234"), "5565999991234")
        self.assertEqual(wa.normalizar_telefone("+55 65 99999-1234"), "5565999991234")
        self.assertEqual(wa.normalizar_telefone("065 99999-1234"), "5565999991234")
        for ruim in ("", None, "99999-1234", "(65) 3322-1234", "123", "(65) 89999-1234", "(05) 99999-1234"):
            self.assertIsNone(wa.normalizar_telefone(ruim), ruim)

    def test_mascara_e_parametro(self):
        self.assertNotIn("99999", wa.mascarar_telefone("5565999991234"))
        self.assertEqual(wa.limpar_parametro("a\n\nb\t c      d"), "a b c d")


@override_settings(PASSWORD_HASHERS=HASH)
class Base(TestCase):
    def setUp(self):
        self.a, self.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        self.adm = criar_usuario(self.a, "Administrador")
        self.coord = criar_usuario(self.a, "Coordenador")
        self.sec = criar_usuario(self.a, "Secretaria")
        self.prof = criar_usuario(self.a, "Professor")
        self.coord_b = criar_usuario(self.b, "Coordenador")
        self.ano = criar_ano(self.a)
        self.t1 = criar_turma_oficial(self.a, self.ano, numero=1, nome="Turma 1")
        self.t2 = criar_turma_oficial(self.a, self.ano, codigo_serie="EF1-02", numero=1, nome="Turma 2")
        self.r1 = self.resp("Resp Um", "65999990001", self.a, [(self.t1, "A1")])
        self.r2 = self.resp("Resp Dois", "65999990002", self.a, [(self.t2, "A2")])
        self.r3 = self.resp("Resp Dois Filhos", "65999990003", self.a, [(self.t1, "A3"), (self.t2, "A4")])
        self.r4 = self.resp("Sem Telefone", "", self.a, [(self.t1, "A5")])
        self.rb = self.resp("Resp Outra Escola", "65999990009", self.b, [])

    def resp(self, nome, tel, inst, filhos):
        r = Responsavel.objects.create(instituicao=inst, nome=nome, telefone=tel)
        for turma, nome_aluno in filhos:
            al = criar_aluno(inst, self.sec, nome=nome_aluno)
            criar_matricula(aluno=al, turma=turma, usuario=self.sec)
            AlunoResponsavelFinanceiro.objects.create(aluno=al, responsavel=r, parentesco="MAE")
        return r

    def cfg(self, ativo=True, inst=None):
        from apps.billing.crypto import cifrar
        c = ConfiguracaoWhatsApp.obter(inst or self.a)
        c.phone_number_id, c.token_cifrado, c.template_nome, c.ativo = "123456", cifrar(TOKEN), "comunicado_escola", ativo
        c.save()
        return c

    def cli(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        s["escola_id"] = user.perfil.instituicao_id
        s.save()
        return c


class DestinatariosTests(Base):
    def nomes(self, alvo, **kw):
        return {r.nome for r in sv.responsaveis_alvo(self.a, alvo, **kw)}

    def test_turma_unica(self):
        self.assertEqual(self.nomes("TURMAS", turmas=[self.t1]), {"Resp Um", "Resp Dois Filhos", "Sem Telefone"})

    def test_varias_turmas_sem_duplicar(self):
        qs = sv.responsaveis_alvo(self.a, "TURMAS", turmas=[self.t1, self.t2])
        self.assertEqual(sorted(qs.values_list("nome", flat=True)).count("Resp Dois Filhos"), 1)
        self.assertEqual(qs.count(), 4)

    def test_escola_toda_so_da_propria_escola(self):
        n = self.nomes("ESCOLA")
        self.assertIn("Resp Um", n)
        self.assertNotIn("Resp Outra Escola", n)

    def test_um_responsavel(self):
        self.assertEqual(self.nomes("RESPONSAVEL", responsavel=self.r2), {"Resp Dois"})
        self.assertEqual(self.nomes("RESPONSAVEL", responsavel=self.rb), set())  # de outra escola

    def test_turma_de_outra_escola_nao_vaza(self):
        tb = criar_turma_oficial(self.b, criar_ano(self.b), numero=1)
        self.assertEqual(self.nomes("TURMAS", turmas=[tb]), set())

    def test_matricula_cancelada_e_vinculo_encerrado_ficam_fora(self):
        Matricula.objects.filter(aluno__nome="A1").update(status="CANCELADA")
        self.assertNotIn("Resp Um", self.nomes("TURMAS", turmas=[self.t1]))
        AlunoResponsavelFinanceiro.objects.filter(aluno__nome="A2").update(fim=__import__("django").utils.timezone.localdate())
        self.assertNotIn("Resp Dois", self.nomes("ESCOLA"))

    def test_responsavel_inativo_fora(self):
        Responsavel.objects.filter(pk=self.r1.pk).update(ativo=False)
        self.assertNotIn("Resp Um", self.nomes("ESCOLA"))


class EnvioTests(Base):
    def criar(self, **kw):
        d = dict(instituicao=self.a, usuario=self.coord, alvo="TURMAS", mensagem="Reunião amanhã\n às 19h.", turmas=[self.t1])
        d.update(kw)
        return sv.criar_comunicado(**d)

    def test_criar_marca_sem_whatsapp_e_pendentes(self):
        self.cfg()
        c = self.criar()
        st = {e.responsavel.nome: e.status for e in c.envios.all()}
        self.assertEqual(st["Sem Telefone"], "SEM_WHATSAPP")
        self.assertEqual(st["Resp Um"], "PENDENTE")
        self.assertEqual(c.mensagem, "Reunião amanhã às 19h.")
        self.assertNotIn("99999", "".join(c.envios.values_list("telefone_mascarado", flat=True)))

    def test_sem_config_ativa_nao_cria(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.criar()
        self.cfg(ativo=False)
        with self.assertRaises(ValidationError):
            self.criar()

    def test_excluidos_e_sem_destinatario(self):
        from django.core.exceptions import ValidationError
        self.cfg()
        c = self.criar(excluidos={self.r1.pk})
        self.assertFalse(c.envios.filter(responsavel=self.r1).exists())
        with self.assertRaises(ValidationError):
            self.criar(alvo="RESPONSAVEL", responsavel=self.r1, turmas=[], excluidos={self.r1.pk})

    def test_processar_envia_com_modelo_e_telefone_atual(self):
        self.cfg()
        c = self.criar()
        Responsavel.objects.filter(pk=self.r1.pk).update(telefone="(65) 98888-7777")  # mudou depois da prévia
        with mock.patch.object(wa, "_chamar", return_value={"messages": [{"id": "wamid.X"}]}) as ch:
            n = sv.processar(c)
        self.assertEqual(n, 2)
        enviados = c.envios.filter(status="ENVIADO")
        self.assertEqual(enviados.count(), 2)
        self.assertEqual(enviados.first().message_id, "wamid.X")
        corpo = [x.args[3] for x in ch.call_args_list]
        self.assertIn("5565988887777", {b["to"] for b in corpo})
        self.assertTrue(all(b["template"]["name"] == "comunicado_escola" for b in corpo))
        self.assertEqual(corpo[0]["template"]["components"][0]["parameters"][0]["text"], "Reunião amanhã às 19h.")
        self.assertEqual(ch.call_args_list[0].args[1], TOKEN)  # token decifrado só em memória

    def test_falha_de_um_nao_para_os_outros(self):
        self.cfg()
        c = self.criar()
        chamadas = []

        def falso(cfg, token, caminho, corpo=None):
            chamadas.append(corpo["to"])
            if len(chamadas) == 1:
                raise wa.ErroWhatsApp("A Meta recusou (400): número inválido")
            return {"messages": [{"id": "ok"}]}
        with mock.patch.object(wa, "_chamar", side_effect=falso):
            sv.processar(c)
        self.assertEqual(c.envios.filter(status="FALHA").count(), 1)
        self.assertEqual(c.envios.filter(status="ENVIADO").count(), 1)
        self.assertNotIn(TOKEN, "".join(c.envios.values_list("erro", flat=True)))

    def test_lote_e_idempotencia(self):
        self.cfg()
        for i in range(30):
            self.resp(f"Extra {i}", f"659999{i:05d}", self.a, [(self.t1, f"AX{i}")])
        c = self.criar()
        with mock.patch.object(wa, "_chamar", return_value={"messages": [{"id": "m"}]}):
            self.assertEqual(sv.processar(c), 25)
            self.assertEqual(c.envios.filter(status="PENDENTE").count(), 7)
            sv.processar(c)
            self.assertEqual(sv.processar(c), 0)  # nada reenviado
        self.assertEqual(c.envios.filter(status="ENVIADO").count(), 32)

    def test_limite_por_hora(self):
        from django.core.exceptions import ValidationError
        self.cfg()
        for _ in range(sv.MAX_POR_HORA):
            self.criar()
        with self.assertRaises(ValidationError):
            self.criar()

    def test_comando_processa_fila(self):
        self.cfg()
        c = self.criar()
        with mock.patch.object(wa, "_chamar", return_value={"messages": [{"id": "m"}]}):
            call_command("enviar_comunicados", stdout=io.StringIO())
        self.assertFalse(c.envios.filter(status="PENDENTE").exists())

    def test_erro_http_nao_vaza_token(self):
        cfg = self.cfg()
        corpo = json.dumps({"error": {"message": "Invalid parameter"}}).encode()
        http = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(corpo))
        with mock.patch("urllib.request.urlopen", side_effect=http):
            with self.assertRaises(wa.ErroWhatsApp) as cm:
                wa.enviar_modelo(cfg, TOKEN, "5565999990001", "oi")
        self.assertNotIn(TOKEN, str(cm.exception))
        self.assertIn("Invalid parameter", str(cm.exception))


class TelasTests(Base):
    def test_config_so_administrador(self):
        url = reverse("comunicacao:config")
        for u in (self.coord, self.sec, self.prof):
            self.assertEqual(self.cli(u).get(url).status_code, 403)
        self.assertEqual(self.cli(self.adm).get(url).status_code, 200)
        self.assertEqual(Client().get(url).status_code, 302)

    def test_token_cifrado_e_nunca_exibido(self):
        c = self.cli(self.adm)
        c.post(reverse("comunicacao:config"), {"acao": "salvar", "phone_number_id": "123456", "waba_id": "9", "token": TOKEN,
                                               "template_nome": "comunicado_escola", "template_idioma": "pt_BR", "versao_api": "v21.0"})
        cfg = ConfiguracaoWhatsApp.objects.get(instituicao=self.a)
        self.assertNotIn(TOKEN, cfg.token_cifrado)
        self.assertTrue(cfg.token_cifrado)
        self.assertNotContains(c.get(reverse("comunicacao:config")), TOKEN)
        # salvar sem token mantém o atual
        c.post(reverse("comunicacao:config"), {"acao": "salvar", "phone_number_id": "123456", "template_nome": "x",
                                               "template_idioma": "pt_BR", "versao_api": "v21.0"})
        cfg.refresh_from_db()
        self.assertTrue(cfg.token_cifrado)
        self.assertEqual(cfg.template_nome, "x")

    def test_ativar_exige_teste_e_salvar_desativa(self):
        cfg = self.cfg(ativo=False)
        c = self.cli(self.adm)
        c.post(reverse("comunicacao:config"), {"acao": "ativar"})
        cfg.refresh_from_db()
        self.assertFalse(cfg.ativo)
        with mock.patch.object(wa, "_chamar", return_value={"display_phone_number": "+55 65 9", "verified_name": "Escola"}):
            c.post(reverse("comunicacao:config"), {"acao": "testar"})
        c.post(reverse("comunicacao:config"), {"acao": "ativar"})
        cfg.refresh_from_db()
        self.assertTrue(cfg.ativo)
        c.post(reverse("comunicacao:config"), {"acao": "salvar", "phone_number_id": "123456", "template_nome": "y",
                                               "template_idioma": "pt_BR", "versao_api": "v21.0"})
        cfg.refresh_from_db()
        self.assertFalse(cfg.ativo)
        self.assertIsNone(cfg.testada_em)

    def test_versao_api_e_id_invalidos(self):
        c = self.cli(self.adm)
        r = c.post(reverse("comunicacao:config"), {"acao": "salvar", "phone_number_id": "abc/../x", "template_nome": "x",
                                                   "template_idioma": "pt_BR", "versao_api": "../evil"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ConfiguracaoWhatsApp.objects.filter(instituicao=self.a).exclude(phone_number_id="").exists())

    def test_coordenador_fluxo_previa_confirmar_enviar(self):
        self.cfg()
        c = self.cli(self.coord)
        self.assertEqual(c.get(reverse("comunicacao:lista")).status_code, 200)
        dados = {"alvo": "TURMAS", "turmas": [self.t1.pk], "mensagem": "Aviso importante"}
        r = c.post(reverse("comunicacao:novo"), dados)
        self.assertContains(r, "Resp Um")
        self.assertNotContains(r, "99999")  # telefone completo nunca aparece
        self.assertFalse(Comunicado.objects.exists())
        r = c.post(reverse("comunicacao:novo"), {**dados, "confirmar": "1", "excluir": [self.r3.pk]})
        com = Comunicado.objects.get()
        self.assertRedirects(r, reverse("comunicacao:detalhe", args=[com.pk]))
        self.assertFalse(com.envios.filter(responsavel=self.r3).exists())
        with mock.patch.object(wa, "_chamar", return_value={"messages": [{"id": "m"}]}):
            c.post(reverse("comunicacao:detalhe", args=[com.pk]))
        self.assertEqual(com.envios.filter(status="ENVIADO").count(), 1)
        acoes = set(RegistroAuditoria.objects.values_list("acao", flat=True))
        self.assertTrue({"COMUNICADO_CRIADO", "COMUNICADO_ENVIADO"} <= acoes)

    def test_validacoes_do_formulario(self):
        self.cfg()
        c = self.cli(self.coord)
        self.assertEqual(c.post(reverse("comunicacao:novo"), {"alvo": "TURMAS", "mensagem": "x"}).status_code, 200)
        self.assertFalse(Comunicado.objects.exists())
        r = c.post(reverse("comunicacao:novo"), {"alvo": "RESPONSAVEL", "responsavel": self.rb.pk, "mensagem": "x"})
        self.assertEqual(r.status_code, 200)  # responsável de outra escola não é opção válida
        self.assertFalse(Comunicado.objects.exists())
        r = c.post(reverse("comunicacao:novo"), {"alvo": "TURMAS", "turmas": [self.t1.pk], "mensagem": "x" * 901})
        self.assertFalse(Comunicado.objects.exists())

    def test_isolamento_do_detalhe(self):
        self.cfg()
        com = sv.criar_comunicado(instituicao=self.a, usuario=self.coord, alvo="ESCOLA", mensagem="oi")
        self.assertEqual(self.cli(self.coord_b).get(reverse("comunicacao:detalhe", args=[com.pk])).status_code, 404)
        outro = criar_usuario(self.b, "Secretaria")
        self.assertEqual(self.cli(outro).post(reverse("comunicacao:detalhe", args=[com.pk])).status_code, 404)

    def test_professor_nao_acessa_comunicados(self):
        for nome in ("comunicacao:lista", "comunicacao:novo"):
            self.assertEqual(self.cli(self.prof).get(reverse(nome)).status_code, 403)
