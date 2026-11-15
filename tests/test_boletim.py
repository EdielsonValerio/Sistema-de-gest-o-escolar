"""Boletim e Histórico escolar: liberação por fase, resultado final, histórico parcial/externo, emissão e isolamento."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.models import ConfiguracaoGeral
from apps.diario import boletim as b, emissao, servicos
from apps.diario.models import HistoricoExterno, HistoricoExternoDisciplina, ResultadoFase, ResultadoFinal
from apps.documents.models import DocumentoEmitido, ModeloDocumento
from apps.documents.services import emitir_documento

from .helpers import criar_instituicao, criar_usuario
from .test_diario import Base


class BoletimBase(Base):
    def fechar(self, periodo, nota="8", ds=date(2027, 3, 10)):
        av = servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
            "periodo": periodo, "titulo": f"Prova {periodo.ordem}", "tipo": "NORMAL", "data": ds,
            "nota_maxima": Decimal(10), "peso": Decimal(1)})
        servicos.lancar_notas(av, {m.pk: Decimal(nota) for m in self.mat}, self.prof1)
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(data=ds),
                             presencas=self.presencas(), finalizar=True, por=self.prof1)
        servicos.fechar_diario(self.aloc, periodo, self.prof1)


class LiberacaoTests(BoletimBase):
    def test_fase_concluida_libera_nota_e_reabrir_remove(self):
        self.assertFalse(ResultadoFase.objects.exists())
        self.fechar(self.p1)
        self.assertEqual(ResultadoFase.objects.filter(matricula=self.mat[0]).count(), 1)
        d = b.dados_boletim(self.mat[0])
        self.assertEqual(d["liberadas"], 1)
        self.assertEqual(d["linhas"][0]["notas"][0], Decimal("8"))
        self.assertIs(d["linhas"][0]["notas"][1], False)
        from apps.diario.models import FechamentoDiario
        f = FechamentoDiario.objects.get(alocacao=self.aloc, periodo=self.p1)
        servicos.reabrir_diario(f, "Erro", self.coord)
        self.assertFalse(ResultadoFase.objects.exists())
        self.assertEqual(b.dados_boletim(self.mat[0])["liberadas"], 0)

    def test_ultima_fase_gera_resultado_final(self):
        self.fechar(self.p1)
        self.assertFalse(ResultadoFinal.objects.exists())
        self.fechar(self.p2, ds=date(2027, 6, 2))
        rf = ResultadoFinal.objects.get(matricula=self.mat[0])
        self.assertIn(rf.resultado, ("APROVADO", "REPROVADO", "PENDENTE"))
        self.assertTrue(b.dados_boletim(self.mat[0])["completo"])

    def test_sincronizacao_e_idempotente(self):
        self.fechar(self.p1)
        n = ResultadoFase.objects.count()
        b.sincronizar_alocacao(self.aloc)
        b.sincronizar_alocacao(self.aloc)
        self.assertEqual(ResultadoFase.objects.count(), n)


class EmissaoTests(BoletimBase):
    def test_sem_fase_concluida_nao_gera(self):
        with self.assertRaises(ValidationError):
            emissao.emitir_boletim(self.mat[0], self.sec)

    def test_gera_boletim_com_modelo_padrao_e_auditoria(self):
        self.fechar(self.p1)
        doc = emissao.emitir_boletim(self.mat[0], self.sec)
        self.assertEqual(doc.tipo, "BOLETIM_ESCOLAR")
        self.assertIn("Matemática", doc.html)
        self.assertIn("<table", doc.html)
        self.assertEqual(ModeloDocumento.objects.filter(tipo="BOLETIM_ESCOLAR", instituicao=self.a).count(), 1)

    def test_tipos_especiais_bloqueados_na_emissao_generica(self):
        self.fechar(self.p1)
        emissao.emitir_boletim(self.mat[0], self.sec)
        modelo = ModeloDocumento.objects.get(tipo="BOLETIM_ESCOLAR")
        with self.assertRaises(ValidationError):
            emitir_documento(modelo=modelo, usuario=self.sec, aluno=self.mat[0].aluno)
        c = self.cli(self.sec)
        r = c.get(reverse("documents:emitir_aluno", args=[self.mat[0].aluno.pk]))
        self.assertContains(r, "Nenhum modelo ativo para aluno")

    def test_telas_botoes_e_permissoes(self):
        self.fechar(self.p1)
        al = self.mat[0].aluno
        for user in (self.sec, self.coord, self.adm):
            c = self.cli(user)
            self.assertContains(c.get(reverse("students:aluno_ficha", args=[al.pk])), "Histórico", msg_prefix=str(user))
            self.assertEqual(c.get(reverse("diario:boletim_aluno", args=[al.pk])).status_code, 200)
            r = c.post(reverse("diario:boletim_aluno", args=[al.pk]), {"matricula": self.mat[0].pk})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(c.get(r["Location"]).status_code, 200, user)
        c = self.cli(self.prof1)
        self.assertEqual(c.get(reverse("diario:boletim_aluno", args=[al.pk])).status_code, 403)
        self.assertEqual(c.get(reverse("diario:historico_aluno", args=[al.pk])).status_code, 403)

    def test_isolamento_entre_escolas(self):
        al = self.mat[0].aluno
        c = self.cli(self.coord_b)
        self.assertEqual(c.get(reverse("diario:boletim_aluno", args=[al.pk])).status_code, 404)
        self.assertEqual(c.get(reverse("diario:historico_aluno", args=[al.pk])).status_code, 404)


class HistoricoTests(BoletimBase):
    def test_parcial_desabilitado_omite_ano_em_andamento(self):
        self.fechar(self.p1)
        d = b.dados_historico(self.mat[0].aluno)
        self.assertEqual(d["blocos"], [])
        with self.assertRaises(ValidationError):
            emissao.emitir_historico(self.mat[0].aluno, self.sec)

    def test_parcial_habilitado_inclui_ano_em_andamento(self):
        cfg = ConfiguracaoGeral.objects.get_or_create(instituicao=self.a)[0]
        cfg.historico_parcial = True
        cfg.save()
        self.fechar(self.p1)
        d = b.dados_historico(self.mat[0].aluno)
        self.assertEqual(len(d["blocos"]), 1)
        doc = emissao.emitir_historico(self.mat[0].aluno, self.sec)
        self.assertEqual(doc.tipo, "HISTORICO_ESCOLAR")
        self.assertIn("Matemática", doc.html)

    def test_historico_externo_pela_tela_e_no_documento(self):
        al = self.mat[0].aluno
        c = self.cli(self.sec)
        r = c.post(reverse("diario:historico_externo_novo", args=[al.pk]), {
            "ano": 2025, "serie": "4º Ano", "escola": "Escola Antiga", "cidade_uf": "Fortaleza/CE",
            "resultado": "APROVADO", "observacoes": "",
            "disciplinas-TOTAL_FORMS": 2, "disciplinas-INITIAL_FORMS": 0, "disciplinas-MIN_NUM_FORMS": 0,
            "disciplinas-MAX_NUM_FORMS": 30,
            "disciplinas-0-nome": "Geografia", "disciplinas-0-media": "9,0", "disciplinas-0-carga_horaria": "80",
            "disciplinas-0-frequencia": "",
            "disciplinas-1-nome": "", "disciplinas-1-media": "", "disciplinas-1-carga_horaria": "",
            "disciplinas-1-frequencia": ""})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        h = HistoricoExterno.objects.get(aluno=al)
        self.assertEqual(h.instituicao, self.a)
        self.assertEqual(HistoricoExternoDisciplina.objects.get(historico=h).instituicao, self.a)
        doc = emissao.emitir_historico(al, self.sec)
        self.assertIn("Escola Antiga", doc.html)
        self.assertIn("Geografia", doc.html)
        # coordenação não cadastra histórico externo
        self.assertEqual(self.cli(self.coord).get(reverse("diario:historico_externo_novo", args=[al.pk])).status_code, 403)
        # outra escola não acessa
        self.assertEqual(self.cli(criar_usuario(self.b, "Secretaria")).get(reverse("diario:historico_externo_editar", args=[al.pk, h.pk])).status_code, 404)

    def test_comando_sincronizar(self):
        from django.core.management import call_command
        self.fechar(self.p1)
        ResultadoFase.objects.all().delete()
        call_command("sincronizar_boletins")
        self.assertTrue(ResultadoFase.objects.exists())


class TelaRenderTests(BoletimBase):
    def test_tabela_renderiza_como_html_nas_telas(self):
        self.fechar(self.p1)
        al = self.mat[0].aluno
        c = self.cli(self.sec)
        r = c.get(reverse("diario:boletim_aluno", args=[al.pk]))
        self.assertContains(r, '<table class="doc-tabela">')
        self.assertNotContains(r, "&lt;table")
        self.assertNotContains(r, "Resultado:</strong> None")
        cfg = ConfiguracaoGeral.objects.get_or_create(instituicao=self.a)[0]
        cfg.historico_parcial = True
        cfg.save()
        r = c.get(reverse("diario:historico_aluno", args=[al.pk]))
        self.assertContains(r, '<table class="doc-tabela">')
        self.assertNotContains(r, "&lt;table")


class TelaTabelaTests(BoletimBase):
    def test_tabela_renderiza_como_html_e_resultado_em_andamento(self):
        c = self.cli(self.sec)
        r = c.get(reverse("diario:boletim_aluno", args=[self.mat[0].aluno.pk]))
        self.assertContains(r, '<table class="doc-tabela">')
        self.assertNotContains(r, "&lt;table")
        self.assertNotContains(r, "Resultado:</strong> None")
        self.assertContains(r, "Em andamento")
        cfg = ConfiguracaoGeral.objects.get_or_create(instituicao=self.a)[0]
        cfg.historico_parcial = True
        cfg.save()
        self.fechar(self.p1)
        r = c.get(reverse("diario:historico_aluno", args=[self.mat[0].aluno.pk]))
        self.assertNotContains(r, "&lt;table")


class ImprimirTests(BoletimBase):
    def test_botao_imprimir_abre_documento_com_impressao_automatica(self):
        self.fechar(self.p1)
        c = self.cli(self.sec)
        url = reverse("diario:boletim_aluno", args=[self.mat[0].aluno.pk])
        self.assertContains(c.get(url), "Imprimir / salvar em PDF")
        r = c.post(url, {"matricula": self.mat[0].pk, "imprimir": "1"})
        self.assertIn("imprimir=1", r["Location"])
        self.assertContains(c.get(r["Location"]), "window.print()", count=2)  # botão + disparo automático
        r2 = c.post(url, {"matricula": self.mat[0].pk})
        self.assertNotIn("imprimir=1", r2["Location"])


class BotoesSempreVisiveisTests(BoletimBase):
    def test_botoes_aparecem_desabilitados_sem_fase_concluida(self):
        c = self.cli(self.sec)
        r = c.get(reverse("diario:boletim_aluno", args=[self.mat[0].aluno.pk]))
        self.assertContains(r, "Gerar boletim")
        self.assertContains(r, "Imprimir / salvar em PDF")
        r = c.get(reverse("diario:historico_aluno", args=[self.mat[0].aluno.pk]))
        self.assertContains(r, "Gerar histórico")
