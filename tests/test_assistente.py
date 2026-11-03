import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.assistente import ferramentas, lgpd, servicos
from apps.assistente.cliente import ClienteClaude, ClienteDemo, ErroIA
from apps.assistente.models import ConfiguracaoIA, Conversa, Mensagem, UsoMensal
from apps.audit.models import RegistroAuditoria
from apps.enrollments.services import criar_matricula
from apps.finance import services as fin
from apps.students.models import Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_plano, criar_turma_oficial, criar_usuario

CPF = "529.982.247-25"
CHAVE = "sk-ant-chave-secreta-123"


class Falso:
    """Cliente de IA falso: devolve respostas roteirizadas e guarda o que recebeu."""

    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), []

    def mensagem(self, **kw):
        self.chamadas.append(json.loads(json.dumps(kw, default=str)))
        return self.respostas.pop(0)


def texto(t, ent=10, sai=5):
    return {"content": [{"type": "text", "text": t}], "stop_reason": "end_turn",
            "usage": {"input_tokens": ent, "output_tokens": sai}}


def usa(nome, entrada=None, id_="t1"):
    return {"content": [{"type": "tool_use", "id": id_, "name": nome, "input": entrada or {}}],
            "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5}}


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class Base(TestCase):
    def setUp(self):
        self.inst = criar_instituicao()
        self.outra = criar_instituicao("Outra Escola")
        self.adm = criar_usuario(self.inst, "Administrador")
        self.fin = criar_usuario(self.inst, "Financeiro")
        self.sec = criar_usuario(self.inst, "Secretaria")
        self.cons = criar_usuario(self.inst, "Consulta")
        self.sistema = User.objects.create_superuser("sis", "s@x.com", "senha-sis-123")

    def pronta(self, inst=None):
        inst = inst or self.inst
        cfg = servicos.configuracao(inst)
        cfg.habilitada, cfg.ativa = True, True
        cfg.definir_api_key(CHAVE)
        cfg.termo_aceito_em, cfg.testada_em = timezone.now(), timezone.now()
        cfg.save()
        return cfg

    def logar(self, user):
        c = Client()
        c.force_login(user)
        s = c.session
        if hasattr(user, "perfil"):
            s["escola_id"] = user.perfil.instituicao_id
        s.save()
        return c

    def dados(self, inst=None, n_alunos=3, pagar=True):
        """Escola com `n_alunos` matriculados numa turma, parcelas e um recebimento."""
        inst = inst or self.inst
        turma = criar_turma_oficial(inst, numero=7, nome="Turma Sigilosa 7")
        plano = criar_plano(inst, turma, total="300.00", parcelas=3, primeira=timezone.localdate() - timedelta(days=40), dia=10)
        resp = Responsavel.objects.create(instituicao=inst, nome="Maria Responsável Secreta", documento=CPF)
        usuario = criar_usuario(inst, "Secretaria", username=f"sec-d-{inst.pk}")
        alunos = []
        for i in range(n_alunos):
            a = criar_aluno(inst, usuario, nome=f"Fulano Sigiloso {i}")
            m = criar_matricula(aluno=a, turma=turma, usuario=usuario)
            with self.captureOnCommitCallbacks(execute=True):
                fin.contratar_plano(matricula=m, plano=plano, responsavel=resp, usuario=usuario)
            alunos.append(a)
        return turma, alunos


class LgpdTests(TestCase):
    def test_redige_cpf_cnpj_email_telefone(self):
        t, ok = lgpd.redigir("CPF 529.982.247-25, cnpj 11.222.333/0001-81, a@b.com, (65) 99999-1234")
        self.assertTrue(ok)
        for s in ("529.982", "0001-81", "a@b.com", "99999-1234"):
            self.assertNotIn(s, t)

    def test_texto_comum_nao_muda(self):
        t, ok = lgpd.redigir("gráfico de matrículas dos últimos 12 meses")
        self.assertFalse(ok)
        self.assertEqual(t, "gráfico de matrículas dos últimos 12 meses")

    def test_agrupa_grupos_pequenos(self):
        linhas = [{"t": "A", "n": 5}, {"t": "B", "n": 2}, {"t": "C", "n": 1}]
        out, omitido = lgpd.agrupar_pequenos(linhas, "t", "n")
        self.assertEqual({l["t"]: l["n"] for l in out}, {"A": 5, lgpd.OUTROS: 3})
        self.assertFalse(omitido)

    def test_omite_outros_ainda_pequeno(self):
        out, omitido = lgpd.agrupar_pequenos([{"t": "A", "n": 5}, {"t": "B", "n": 2}], "t", "n")
        self.assertEqual([l["t"] for l in out], ["A"])
        self.assertTrue(omitido)


class FerramentasTests(Base):
    def test_matriculas_por_turma_sem_nomes_e_com_agrupamento(self):
        self.dados(n_alunos=3)
        r = ferramentas.matriculas_por_turma(self.inst, {})
        self.assertEqual(r["linhas"], [{"turma": "Turma Sigilosa 7", "matriculas_ativas": 3}])
        self.assertNotIn("Fulano", json.dumps(r))

    def test_turma_com_menos_de_3_alunos_e_suprimida(self):
        self.dados(n_alunos=2)
        r = ferramentas.matriculas_por_turma(self.inst, {})
        self.assertEqual(r["linhas"], [])

    def test_inadimplencia_agregada(self):
        self.dados(n_alunos=3)
        r = ferramentas.inadimplencia_por_turma(self.inst, {})
        self.assertEqual(r["linhas"][0]["alunos_inadimplentes"], 3)
        self.assertGreater(r["linhas"][0]["valor_vencido"], 0)
        self.assertNotIn("Fulano", json.dumps(r))
        self.assertNotIn("Maria", json.dumps(r))

    def test_isolamento_entre_escolas(self):
        self.dados(self.inst, n_alunos=3)
        r = ferramentas.matriculas_por_turma(self.outra, {})
        self.assertEqual(r["linhas"], [])
        self.assertEqual(ferramentas.inadimplencia_por_turma(self.outra, {})["linhas"], [])

    def test_series_mensais_tem_n_meses_e_limite(self):
        r = ferramentas.evolucao_matriculas_e_recebimentos(self.inst, {"meses": 6})
        self.assertEqual(len(r["linhas"]), 6)
        self.assertEqual(len(ferramentas.recebimentos_por_mes(self.inst, {"meses": 999})["linhas"]), ferramentas.MAX_MESES)
        self.assertEqual(len(ferramentas.recebimentos_por_mes(self.inst, {"meses": "x"})["linhas"]), 12)

    def test_contas_a_pagar_sem_fornecedor(self):
        r = ferramentas.contas_a_pagar_resumo(self.inst, {})
        self.assertIn("total_em_aberto", r)

    def test_ferramentas_filtradas_por_permissao(self):
        self.assertEqual(set(ferramentas.disponiveis(self.adm)), set(ferramentas.FERRAMENTAS))
        self.assertEqual(set(ferramentas.disponiveis(self.cons)),
                         {"matriculas_por_turma", "inadimplencia_por_turma", "a_receber_por_mes",
                          "contas_a_pagar_resumo", "recebimentos_por_mes", "evolucao_matriculas_e_recebimentos"})
        sec = set(ferramentas.disponiveis(self.sec))
        self.assertIn("matriculas_por_turma", sec)
        self.assertNotIn("recebimentos_por_mes", sec)
        self.assertNotIn("contas_a_pagar_resumo", sec)

    def test_sem_permissoes_nao_oferece_grafico(self):
        u = User.objects.create_user("nada", password="x")
        self.assertEqual(ferramentas.definicoes(u), [])

    def test_validar_grafico(self):
        ok = ferramentas.validar_grafico({"titulo": "T", "rotulos": ["a", "b"], "series": [
            {"nome": "S", "valores": [1, 2.5], "tipo": "xyz", "eixo": "direita", "unidade": "R$"}]})
        self.assertEqual(ok["series"][0]["tipo"], "bar")
        self.assertEqual(ok["series"][0]["eixo"], "direita")
        for ruim in [None, {"rotulos": [], "series": []}, {"rotulos": ["a"], "series": [{"nome": "s", "valores": [1, 2]}]},
                     {"rotulos": ["a"], "series": [{"nome": "s", "valores": ["<script>"]}]},
                     {"rotulos": ["a"], "series": [{"nome": "s", "valores": [True]}]},
                     {"rotulos": ["a"] * 41, "series": [{"nome": "s", "valores": [1] * 41}]}]:
            with self.assertRaises(ValueError):
                ferramentas.validar_grafico(ruim)


class ClienteTests(TestCase):
    def test_monta_requisicao_e_nao_vaza_chave_em_erro(self):
        c = ClienteClaude(CHAVE, "claude-sonnet-5-5")
        with mock.patch.object(ClienteClaude, "_post", return_value={"content": []}) as p:
            c.mensagem(system="s", mensagens=[{"role": "user", "content": "oi"}], ferramentas=[{"name": "x"}])
        corpo = p.call_args[0][0]
        self.assertEqual(corpo["model"], "claude-sonnet-5-5")
        self.assertEqual(corpo["tools"], [{"name": "x"}])
        self.assertNotIn(CHAVE, json.dumps(corpo))

    def test_mensagens_http_amigaveis(self):
        import urllib.error
        for cod, trecho in [(401, "recusada"), (429, "limite"), (500, "indisponível"), (404, "Modelo")]:
            exc = urllib.error.HTTPError("u", cod, "x", {}, None)
            self.assertIn(trecho, ClienteClaude._mensagem_http(exc))
            self.assertNotIn(CHAVE, ClienteClaude._mensagem_http(exc))

    def test_falha_de_rede_vira_erro_ia(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
            with self.assertRaises(ErroIA):
                ClienteClaude(CHAVE, "m").mensagem(system="s", mensagens=[], ferramentas=[])


class ServicosTests(Base):
    def conversa(self, user=None):
        return Conversa.objects.create(instituicao=self.inst, usuario=user or self.adm)

    def test_fluxo_com_ferramenta_e_grafico(self):
        self.pronta()
        self.dados()
        falso = Falso(
            usa("evolucao_matriculas_e_recebimentos", {"meses": 3}),
            usa("emitir_grafico", {"titulo": "G", "rotulos": ["a"], "series": [{"nome": "s", "valores": [1]}]}, "t2"),
            texto("Pronto", 7, 3))
        msg = servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="gráfico", cliente=falso)
        self.assertEqual(msg.texto, "Pronto")
        self.assertEqual(msg.grafico["titulo"], "G")
        self.assertEqual(msg.ferramentas, ["evolucao_matriculas_e_recebimentos", "emitir_grafico"])
        self.assertEqual((msg.tokens_entrada, msg.tokens_saida), (27, 13))
        # o resultado da ferramenta chegou ao modelo e não tem nomes de pessoas
        enviado = json.dumps(falso.chamadas[1]["mensagens"])
        self.assertIn("alunos_matriculados", enviado)
        self.assertNotIn("Fulano", enviado)
        uso = UsoMensal.objects.get(instituicao=self.inst)
        self.assertEqual((uso.perguntas, uso.tokens), (1, 40))

    def test_dados_pessoais_na_pergunta_sao_removidos_antes_do_envio(self):
        self.pronta()
        falso = Falso(texto("ok"))
        msg = servicos.responder(conversa=self.conversa(), usuario=self.adm,
                                 pergunta=f"o CPF {CPF} deve R$?", cliente=falso)
        self.assertNotIn("529.982", json.dumps(falso.chamadas[0]["mensagens"]))
        self.assertIn("removidos", msg.aviso)
        self.assertNotIn("529.982", Mensagem.objects.filter(papel="USUARIO").first().texto)

    def test_auditoria_nao_guarda_o_texto_da_pergunta(self):
        self.pronta()
        servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="pergunta muito confidencial xyz",
                           cliente=Falso(texto("ok")))
        reg = RegistroAuditoria.objects.get(acao="IA_PERGUNTA")
        self.assertNotIn("confidencial", json.dumps(reg.dados) + reg.descricao)

    def test_ferramenta_fora_da_permissao_e_recusada(self):
        self.pronta()
        falso = Falso(usa("recebimentos_por_mes"), texto("sem acesso"))
        servicos.responder(conversa=self.conversa(self.sec), usuario=self.sec, pergunta="recebido?", cliente=falso)
        resultado = falso.chamadas[1]["mensagens"][-1]["content"][0]
        self.assertTrue(resultado.get("is_error"))
        self.assertIn("indisponível", resultado["content"])

    def test_grafico_invalido_volta_erro_ao_modelo(self):
        self.pronta()
        falso = Falso(usa("emitir_grafico", {"titulo": "x", "rotulos": ["a"], "series": [{"nome": "s", "valores": ["a"]}]}),
                      texto("falhou"))
        msg = servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="g", cliente=falso)
        self.assertIsNone(msg.grafico)
        self.assertTrue(falso.chamadas[1]["mensagens"][-1]["content"][0].get("is_error"))

    def test_limite_de_passos(self):
        self.pronta()
        falso = Falso(*[usa("matriculas_por_turma", id_=f"t{i}") for i in range(servicos.MAX_ITERACOES)])
        msg = servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="loop", cliente=falso)
        self.assertIn("passos", msg.aviso)

    def test_limite_mensal_bloqueia(self):
        cfg = self.pronta()
        cfg.limite_mensal_tokens = 20
        cfg.save()
        UsoMensal.objects.create(instituicao=self.inst, mes=servicos.mes_atual(), tokens_entrada=20)
        with self.assertRaises(ValidationError):
            servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="oi", cliente=Falso(texto("x")))

    def test_nao_pronta_ou_sem_permissao(self):
        cfg = self.pronta()
        with self.assertRaises(Exception):
            servicos.responder(conversa=self.conversa(self.cons), usuario=self.cons, pergunta="oi", cliente=Falso(texto("x")))
        cfg.ativa = False
        cfg.save()
        with self.assertRaises(ValidationError):
            servicos.responder(conversa=self.conversa(), usuario=self.adm, pergunta="oi", cliente=Falso(texto("x")))

    def test_erro_do_servico_nao_grava_nada(self):
        self.pronta()
        class Quebra:
            def mensagem(self, **kw):
                raise ErroIA("falha")
        c = self.conversa()
        with self.assertRaises(ErroIA):
            servicos.responder(conversa=c, usuario=self.adm, pergunta="oi", cliente=Quebra())
        self.assertEqual(Mensagem.objects.count(), 0)
        self.assertFalse(UsoMensal.objects.exists())

    def test_historico_limitado_e_comeca_com_usuario(self):
        self.pronta()
        c = self.conversa()
        for i in range(20):
            Mensagem.objects.create(instituicao=self.inst, conversa=c, papel="USUARIO" if i % 2 == 0 else "ASSISTENTE", texto=f"m{i}")
        falso = Falso(texto("ok"))
        servicos.responder(conversa=c, usuario=self.adm, pergunta="nova", cliente=falso)
        msgs = falso.chamadas[0]["mensagens"]
        self.assertEqual(msgs[0]["role"], "user")
        self.assertLessEqual(len(msgs), servicos.HISTORICO + 1)
        self.assertTrue(all(a["role"] != b["role"] for a, b in zip(msgs, msgs[1:])))

    def test_modo_demo_gera_grafico_de_matriculas_e_recebimentos(self):
        cfg = self.pronta()
        cfg.definir_api_key("DEMO")
        cfg.save()
        self.dados()
        msg = servicos.responder(conversa=self.conversa(), usuario=self.adm,
                                 pergunta="gráfico de alunos matriculados e valor recebido")
        self.assertEqual({s["tipo"] for s in msg.grafico["series"]}, {"bar", "line"})
        self.assertIn("demonstração", msg.texto)
        self.assertIsInstance(servicos.cliente_para(cfg), ClienteDemo)

    def test_ativar_exige_requisitos(self):
        cfg = servicos.configuracao(self.inst)
        with self.assertRaises(ValidationError):
            servicos.ativar(cfg, True, self.adm)  # não habilitada
        cfg = servicos.habilitar_pelo_sistema(self.inst, True, self.sistema)
        with self.assertRaises(ValidationError):
            servicos.ativar(cfg, True, self.adm)  # sem chave
        servicos.salvar_integracao(cfg, api_key=CHAVE, modelo="claude-haiku-5-5", limite=0, aceitar_termo=True, usuario=self.adm)
        with self.assertRaises(ValidationError):
            servicos.ativar(cfg, True, self.adm)  # não testada
        servicos.testar_conexao(cfg, cliente=Falso(texto("ok")))
        servicos.ativar(cfg, True, self.adm)
        self.assertTrue(cfg.pronta)
        servicos.salvar_integracao(cfg, api_key="sk-nova", modelo="claude-haiku-5-5", limite=0, aceitar_termo=True, usuario=self.adm)
        self.assertFalse(cfg.ativa)  # chave nova exige novo teste

    def test_chave_gravada_cifrada(self):
        cfg = self.pronta()
        self.assertNotIn(CHAVE, cfg.api_key_cifrada)
        self.assertEqual(cfg.api_key, CHAVE)
        self.assertNotIn(CHAVE, json.dumps(list(RegistroAuditoria.objects.values_list("dados", flat=True)), default=str))

    def test_desabilitar_pelo_sistema_desativa(self):
        self.pronta()
        servicos.habilitar_pelo_sistema(self.inst, False, self.sistema)
        cfg = ConfiguracaoIA.objects.get(instituicao=self.inst)
        self.assertFalse(cfg.habilitada)
        self.assertFalse(cfg.pronta)


class TelasTests(Base):
    def test_escola_nao_habilitada_nao_acessa(self):
        c = self.logar(self.adm)
        for nome in ("assistente:chat", "assistente:configuracao"):
            self.assertEqual(c.get(reverse(nome)).status_code, 403, nome)
        self.assertNotContains(c.get(reverse("core:dashboard")) if False else c.get("/"), "Assistente de IA")

    def test_menu_e_configuracao_quando_habilitada(self):
        servicos.habilitar_pelo_sistema(self.inst, True, self.sistema)
        c = self.logar(self.adm)
        self.assertContains(c.get(reverse("assistente:configuracao")), "Assistente de IA")
        self.assertEqual(c.get(reverse("assistente:chat")).status_code, 403)  # ainda não ativa
        # só administrador configura
        self.assertEqual(self.logar(self.fin).get(reverse("assistente:configuracao")).status_code, 403)

    def test_configurar_pela_tela_com_demo(self):
        servicos.habilitar_pelo_sistema(self.inst, True, self.sistema)
        c = self.logar(self.adm)
        url = reverse("assistente:configuracao")
        r = c.post(url, {"acao": "salvar", "api_key": "DEMO", "modelo": "claude-sonnet-5-5",
                         "limite_mensal_tokens": "1000", "aceitar_termo": "on"})
        self.assertEqual(r.status_code, 302)
        c.post(url, {"acao": "testar"})
        c.post(url, {"acao": "ativar"})
        self.assertTrue(ConfiguracaoIA.objects.get(instituicao=self.inst).pronta)
        pagina = c.get(url)
        self.assertContains(pagina, "Chave gravada: <code>••")  # a chave é exibida mascarada
        self.assertNotContains(pagina, "Chave gravada: <code>DEMO")
        self.assertContains(c.get(reverse("assistente:chat")), "ia-form")

    def test_sem_aceitar_termo_nao_salva(self):
        servicos.habilitar_pelo_sistema(self.inst, True, self.sistema)
        c = self.logar(self.adm)
        r = c.post(reverse("assistente:configuracao"), {"acao": "salvar", "api_key": "DEMO",
                                                         "modelo": "claude-sonnet-5-5", "limite_mensal_tokens": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(servicos.configuracao(self.inst).tem_api_key)

    def test_chat_perguntar_json(self):
        cfg = self.pronta()
        cfg.definir_api_key("DEMO")
        cfg.save()
        self.dados()
        c = self.logar(self.fin)
        r = c.post(reverse("assistente:perguntar"), json.dumps({"pergunta": "inadimplência por turma"}),
                   content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j["grafico"]["series"][0]["nome"], "Valor vencido")
        self.assertNotIn("Fulano", r.content.decode())
        # a conversa só é visível ao dono
        outra = self.logar(self.adm)
        self.assertEqual(outra.get(reverse("assistente:chat") + f"?c={j['conversa']}").status_code, 404)
        r2 = outra.post(reverse("assistente:perguntar"), json.dumps({"pergunta": "x", "conversa": j["conversa"]}),
                        content_type="application/json")
        self.assertEqual(r2.status_code, 404)

    def test_perguntar_erros(self):
        cfg = self.pronta()
        cfg.definir_api_key("DEMO")
        cfg.save()
        c = self.logar(self.fin)
        url = reverse("assistente:perguntar")
        self.assertEqual(c.post(url, "nao-json", content_type="application/json").status_code, 400)
        self.assertEqual(c.post(url, json.dumps({"pergunta": "  "}), content_type="application/json").status_code, 400)
        self.assertEqual(c.post(url, json.dumps({"pergunta": 5}), content_type="application/json").status_code, 400)
        self.assertEqual(self.logar(self.cons).post(url, json.dumps({"pergunta": "oi"}),
                                                     content_type="application/json").status_code, 403)
        self.assertEqual(Client().post(url, "{}", content_type="application/json").status_code, 302)

    def test_isolamento_de_escolas_no_chat(self):
        cfg = self.pronta()
        cfg.definir_api_key("DEMO")
        cfg.save()
        self.pronta(self.outra)
        user_b = criar_usuario(self.outra, "Financeiro", username="fin-b")
        c = self.logar(self.fin)
        j = c.post(reverse("assistente:perguntar"), json.dumps({"pergunta": "oi"}),
                   content_type="application/json")
        cid = Conversa.objects.get(instituicao=self.inst).pk
        self.assertEqual(self.logar(user_b).get(reverse("assistente:chat") + f"?c={cid}").status_code, 404)

    def test_excluir_conversa_so_do_dono(self):
        self.pronta()
        conv = Conversa.objects.create(instituicao=self.inst, usuario=self.fin)
        self.assertEqual(self.logar(self.adm).post(reverse("assistente:conversa_excluir", args=[conv.pk])).status_code, 404)
        self.assertEqual(self.logar(self.fin).post(reverse("assistente:conversa_excluir", args=[conv.pk])).status_code, 302)
        self.assertFalse(Conversa.objects.filter(pk=conv.pk).exists())


class CadastroEscolaTests(Base):
    def test_sistema_habilita_na_edicao_da_escola(self):
        c = Client()
        c.force_login(self.sistema)
        url = reverse("core:escola_editar", args=[self.inst.pk])
        base = {"nome": self.inst.nome, "cnpj": "", "ativa": "on"}
        self.assertContains(c.get(url), "Habilitar o assistente de IA")
        c.post(url, {**base, "ia_habilitada": "on"})
        self.assertTrue(ConfiguracaoIA.objects.get(instituicao=self.inst).habilitada)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="IA_HABILITADA", instituicao=self.inst).exists())
        c.post(url, base)
        self.assertFalse(ConfiguracaoIA.objects.get(instituicao=self.inst).habilitada)

    def test_administrador_da_escola_nao_acessa_cadastro_de_escolas(self):
        self.assertEqual(self.logar(self.adm).get(reverse("core:escola_editar", args=[self.inst.pk])).status_code, 403)
