"""Modelo de avaliação configurável: fórmulas seguras, fases/componentes, recuperações, aprovação, série e permissões."""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.academic.models import AnoLetivo, SerieReferencia
from apps.audit.models import RegistroAuditoria
from apps.diario import calculos, formulas, motor, servicos
from apps.diario.models import Avaliacao, ComponenteNota, ModeloAvaliacao, Nota, PeriodoLetivo

from .test_diario import HASH, Base


class FormulasTests(TestCase):
    def test_calculos_basicos(self):
        v = {"ATV": D("3.5"), "PROVA": D("5"), "B1": D(6), "B2": D(8)}
        self.assertEqual(formulas.avaliar("atv + prova", v), D("8.5"))
        self.assertEqual(formulas.avaliar("(B1 + B2) / 2", v), D(7))
        self.assertEqual(formulas.avaliar("SE(B1 >= 6 and B2 >= 6, 10, 0)", v), D(10))
        self.assertEqual(formulas.avaliar("ARRED(MAX(ATV, PROVA) * 1.5, 1)", v), D("7.5"))
        self.assertEqual(formulas.avaliar("MEDIA(B1, B2, 4)", v), D(6))
        self.assertEqual(formulas.avaliar("B1 ** 2", v), D(36))

    def test_fórmulas_perigosas_ou_invalidas_sao_recusadas(self):
        for ruim in ("__import__('os').system('ls')", "ATV.real", "().__class__", "open('x')", "lambda: 1", "[1, 2]",
                     "XYZ + 1", "1/0", "PROVA ** 9", "ATV > 1", "", "SE(1, 2)", "ATV +", "0,5 * ATV", "x" * 400,
                     "ATV if 1 else 2", "exec('1')", "ATV; 1"):
            self.assertIsNotNone(formulas.validar(ruim, ["ATV", "PROVA"]), ruim)

    def test_siglas(self):
        for ok in ("ATV", "PROVA", "T1", "TRAB_2"):
            self.assertTrue(formulas.sigla_valida(ok), ok)
        for ruim in ("", "1A", "atv", "MF", "MAX", "B1", "RF", "A" * 13, "A B"):
            self.assertFalse(formulas.sigla_valida(ruim), ruim)


@override_settings(PASSWORD_HASHERS=HASH)
class AvaliacaoBase(Base):
    def setUp(self):
        super().setUp()
        self.su = User.objects.create_superuser("root", "r@x.com", "senha-root-123")
        self.serie5 = SerieReferencia.objects.get(codigo="EF1-01")  # série da self.turma
        self.m = servicos.criar_de_preset(self.a, self.ano, "Bimestral ATV+PROVA", "bim_atv_prova", self.su)
        self.m.series.add(self.serie5)

    def suporte(self, escola=None):
        c = Client()
        c.force_login(self.su)
        s = c.session
        s["escola_id"] = 0
        s.save()
        c.post(reverse("core:escola_acessar", args=[(escola or self.a).pk]))
        return c

    def publicar(self):
        servicos.publicar_modelo(self.m, self.su)
        self.aloc = type(self.aloc).objects.get(pk=self.aloc.pk)  # limpa cache do modelo
        return self.m

    def fase(self, i=1):
        return self.m.fases.get(ordem=i)

    def comp(self, sigla, i=1):
        return ComponenteNota.objects.get(fase=self.fase(i), sigla=sigla)

    def av(self, titulo, sigla, ordem=1, maxima="10", peso="1", tipo="NORMAL"):
        return servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
            "periodo": self.fase(ordem), "componente": self.comp(sigla, ordem) if sigla else None, "titulo": titulo, "tipo": tipo,
            "data": date(2027, 3, 20), "nota_maxima": D(maxima), "peso": D(peso)})

    def linha(self, mat):
        _, linhas = calculos.desempenho_da_alocacao(type(self.aloc).objects.get(pk=self.aloc.pk))
        return next(l for l in linhas if l["matricula"].pk == mat.pk)


class MotorTests(AvaliacaoBase):
    def test_preset_cria_estrutura(self):
        self.assertEqual(self.m.fases.count(), 4)
        self.assertEqual(ComponenteNota.objects.filter(fase__modelo=self.m).count(), 8)
        self.assertTrue(self.m.usa_rec_parcial and self.m.usa_rec_final)
        self.assertEqual(servicos.validar_modelo(self.m)[0], [])

    def test_composicao_atividade_mais_prova(self):
        self.publicar()
        a1 = self.av("Atividade 1", "ATV", maxima="2")
        a2 = self.av("Atividade 2", "ATV", maxima="3")
        p = self.av("Prova", "PROVA", maxima="10")
        servicos.lancar_notas(a1, {self.mat[0].pk: D("2")}, self.prof1)
        servicos.lancar_notas(a2, {self.mat[0].pk: D("3")}, self.prof1)   # ATV soma 5, limitada a 4,0
        servicos.lancar_notas(p, {self.mat[0].pk: D("10")}, self.prof1)  # PROVA soma 10, limitada a 6,0
        r = self.linha(self.mat[0])
        self.assertEqual(r["medias"][0], D("10.00"))
        self.assertEqual(r["situacao"], "Em andamento")  # faltam as outras fases

    def test_agregacoes(self):
        c = self.comp("PROVA")
        itens = [(D(5), D(10), D(1)), (D(10), D(10), D(3))]
        for ag, esperado in (("SOMA", D(6)), ("MEDIA", D("4.5")), ("PONDERADA", D("5.25")), ("MAIOR", D(6)),
                             ("DESC_MENOR", D(6))):
            c.agregacao = ag
            self.assertEqual(round(motor.agregar(c, itens), 2), esperado, ag)
        self.assertIsNone(motor.agregar(c, []))

    def test_ano_completo_aprovado_e_reprovado(self):
        self.publicar()
        for ordem in (1, 2, 3, 4):
            a = self.av(f"A{ordem}", "ATV", ordem, maxima="4")
            p = self.av(f"P{ordem}", "PROVA", ordem, maxima="6")
            servicos.lancar_notas(a, {self.mat[0].pk: D(4), self.mat[1].pk: D(1), self.mat[2].pk: D(3)}, self.prof1)
            servicos.lancar_notas(p, {self.mat[0].pk: D(6), self.mat[1].pk: D(1), self.mat[2].pk: D(3)}, self.prof1)
        self.assertEqual((self.linha(self.mat[0])["media_anual"], self.linha(self.mat[0])["situacao"]), (D("10.00"), "Aprovado"))
        self.assertEqual(self.linha(self.mat[1])["media_anual"], D("2.00"))
        self.assertEqual(self.linha(self.mat[1])["situacao"], "Recuperação final")  # abaixo de 6 e escola tem recuperação final
        self.assertEqual(self.linha(self.mat[2])["situacao"], "Aprovado")  # 3+3 = 6,0 em todas as fases

    def test_recuperacao_final_aprova_ou_reprova(self):
        self.publicar()
        for ordem in (1, 2, 3, 4):
            a = self.av(f"A{ordem}", "ATV", ordem, maxima="4")
            p = self.av(f"P{ordem}", "PROVA", ordem, maxima="6")
            servicos.lancar_notas(a, {self.mat[1].pk: D(2), self.mat[2].pk: D(2)}, self.prof1)
            servicos.lancar_notas(p, {self.mat[1].pk: D(2), self.mat[2].pk: D(2)}, self.prof1)   # média 4,0
        rec = self.av("Recuperação final", None, tipo="REC_FINAL")
        self.assertEqual(rec.periodo, self.fase(4))  # fica na última fase
        servicos.lancar_notas(rec, {self.mat[1].pk: D(9), self.mat[2].pk: D(5)}, self.prof1)
        # padrão: MAX(MF, (MF + RF) / 2) -> aluno1 = 6,5 (aprovado) ; aluno2 = 4,5 (reprovado)
        r1, r2 = self.linha(self.mat[1]), self.linha(self.mat[2])
        self.assertEqual((r1["media_final"], r1["situacao"]), (D("6.50"), "Aprovado na recuperação final"))
        self.assertEqual((r2["media_final"], r2["situacao"]), (D("4.50"), "Reprovado"))
        self.m.formula_rec_final = "RF"
        self.m.save()
        self.assertEqual(self.linha(self.mat[1])["media_final"], D("9.00"))

    def test_recuperacao_de_fase_regras(self):
        self.publicar()
        a = self.av("A", "ATV", maxima="4")
        p = self.av("P", "PROVA", maxima="6")
        servicos.lancar_notas(a, {self.mat[0].pk: D(2)}, self.prof1)
        servicos.lancar_notas(p, {self.mat[0].pk: D(2)}, self.prof1)   # fase = 4,0
        rec = self.av("Rec fase 1", None, tipo="RECUPERACAO")
        servicos.lancar_notas(rec, {self.mat[0].pk: D(8)}, self.prof1)
        for regra, esperado in (("MAIOR", D("8.00")), ("SUBSTITUI", D("8.00")), ("MEDIA", D("6.00"))):
            self.m.regra_rec_parcial = regra
            self.m.save()
            self.assertEqual(self.linha(self.mat[0])["medias"][0], esperado, regra)
        servicos.lancar_notas(rec, {self.mat[0].pk: D(1)}, self.prof1)
        self.m.regra_rec_parcial = "MAIOR"
        self.m.save()
        self.assertEqual(self.linha(self.mat[0])["medias"][0], D("4.00"))  # recuperação menor não reduz
        self.m.regra_rec_parcial = "SUBSTITUI"
        self.m.save()
        self.assertEqual(self.linha(self.mat[0])["medias"][0], D("1.00"))

    def test_formula_final_personalizada_e_arredondamento(self):
        f2 = self.fase(2)
        f2.formula = "(ATV + PROVA) * 0.5 + 5"
        f2.save()
        self.publicar()
        for ordem in (1, 2, 3, 4):
            a = self.av(f"A{ordem}", "ATV", ordem, maxima="4")
            p = self.av(f"P{ordem}", "PROVA", ordem, maxima="6")
            servicos.lancar_notas(a, {self.mat[0].pk: D("3.33")}, self.prof1)
            servicos.lancar_notas(p, {self.mat[0].pk: D("4.44")}, self.prof1)
        self.m.formula_final = "MAX(B1, B2) + B3 / 10"
        self.m.casas_decimais = 1
        self.m.save()
        r = self.linha(self.mat[0])
        self.assertEqual(r["medias"][0], D("7.8"))          # 3,33 + 4,44 = 7,77 -> 7,8
        self.assertEqual(r["medias"][1], D("8.9"))          # (7,77 × 0,5) + 5 = 8,885 -> 8,9
        self.assertEqual(r["media_anual"], D("9.7"))        # 8,9 + 7,8 / 10 = 9,68 -> 9,7
        self.m.arredondamento = "BAIXO"
        self.m.save()
        self.assertEqual(self.linha(self.mat[0])["medias"][0], D("7.7"))

    def test_reprovacao_por_frequencia(self):
        self.publicar()
        for ordem in (1, 2, 3, 4):
            a = self.av(f"A{ordem}", "ATV", ordem, maxima="4")
            p = self.av(f"P{ordem}", "PROVA", ordem, maxima="6")
            servicos.lancar_notas(a, {self.mat[0].pk: D(4)}, self.prof1)
            servicos.lancar_notas(p, {self.mat[0].pk: D(6)}, self.prof1)
        for dia, sit in ((10, "P"), (11, "F"), (12, "F")):  # 33% de frequência
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(data=date(2027, 3, dia)),
                                 presencas={self.mat[0].pk: sit, self.mat[1].pk: "P", self.mat[2].pk: "P"}, finalizar=True, por=self.prof1)
        r = self.linha(self.mat[0])
        self.assertEqual(r["situacao"], "Reprovado por frequência")
        self.assertTrue(r["baixa_frequencia"])

    def test_sem_recuperacao_final_reprova_direto(self):
        self.m.usa_rec_final = False
        self.m.save()
        self.publicar()
        for ordem in (1, 2, 3, 4):
            a = self.av(f"A{ordem}", "ATV", ordem, maxima="4")
            servicos.lancar_notas(a, {self.mat[1].pk: D(1)}, self.prof1)
        self.assertEqual(self.linha(self.mat[1])["situacao"], "Reprovado")
        with self.assertRaises(ValidationError):
            self.av("Rec final", None, tipo="REC_FINAL")


class RegrasDoModeloTests(AvaliacaoBase):
    def test_sem_modelo_publicado_vale_a_regra_padrao(self):
        self.assertIsNone(servicos.modelo_da_alocacao(self.aloc))
        a = servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
            "periodo": self.p1, "titulo": "Prova", "tipo": "NORMAL", "data": date(2027, 3, 20), "nota_maxima": D(10), "peso": D(1)})
        servicos.lancar_notas(a, {self.mat[0].pk: D(7)}, self.prof1)
        _, linhas = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual(linhas[0]["situacao"], "Aprovado")

    def test_professor_precisa_informar_componente(self):
        self.publicar()
        with self.assertRaises(ValidationError):
            self.av("Prova sem componente", None)
        outra = self.comp("PROVA", 2)
        with self.assertRaises(ValidationError):
            servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
                "periodo": self.fase(1), "componente": outra, "titulo": "x", "tipo": "NORMAL", "data": date(2027, 3, 20),
                "nota_maxima": D(10), "peso": D(1)})
        self.assertTrue(self.av("Prova", "PROVA"))

    def test_tela_do_professor_oferece_componentes_e_tipos(self):
        self.publicar()
        c = self.cli(self.prof1)
        r = c.get(reverse("diario:avaliacao_nova", args=[self.aloc.pk]))
        self.assertContains(r, "Prova (até 6.00)")
        self.assertContains(r, "Recuperação final")
        r = c.post(reverse("diario:avaliacao_nova", args=[self.aloc.pk]), {
            "periodo": self.fase(1).pk, "componente": "", "titulo": "P1", "tipo": "NORMAL", "data": "2027-03-20",
            "nota_maxima": "10", "peso": "1"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Escolha o componente")
        r = c.post(reverse("diario:avaliacao_nova", args=[self.aloc.pk]), {
            "periodo": self.fase(1).pk, "componente": self.comp("PROVA").pk, "titulo": "P1", "tipo": "NORMAL",
            "data": "2027-03-20", "nota_maxima": "10", "peso": "1"})
        self.assertEqual(r.status_code, 302)
        # o professor só enxerga as fases do modelo (não os períodos gerais)
        self.assertNotContains(c.get(reverse("diario:avaliacao_nova", args=[self.aloc.pk])), f'<option value="{self.p1.pk}">1º Bimestre (2027)')
        d = c.get(reverse("diario:desempenho", args=[self.aloc.pk]))
        self.assertContains(d, "Bimestral ATV+PROVA")

    def test_modelo_por_serie_tem_prioridade_sobre_o_geral(self):
        geral = servicos.criar_de_preset(self.a, self.ano, "Geral", "bim_simples", self.su)
        self.publicar()
        servicos.publicar_modelo(geral, self.su)       # sem séries: vale para as demais
        self.assertEqual(servicos.modelo_da_alocacao(self.aloc), self.m)  # turma da série 1
        a2 = servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma2, disciplina=self.disc, professor=self.prof2, por=self.coord)
        self.assertEqual(servicos.modelo_da_alocacao(a2), geral)          # turma de outra série

    def test_regra_por_serie_com_notas_diferentes(self):
        outro = servicos.criar_de_preset(self.a, self.ano, "Outra série", "bim_simples", self.su)
        outro.series.add(SerieReferencia.objects.get(codigo="EF1-02"))
        outro.media_aprovacao = D("7")
        outro.save()
        self.publicar()
        servicos.publicar_modelo(outro, self.su)
        a2 = servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma2, disciplina=self.disc, professor=self.prof2, por=self.coord)
        self.assertEqual(servicos.criterios_da_alocacao(a2)[0], D("7.00"))
        self.assertEqual(servicos.criterios_da_alocacao(self.aloc)[0], D("6.00"))

    def test_conflito_de_series_ao_publicar(self):
        self.publicar()
        dup = servicos.criar_de_preset(self.a, self.ano, "Duplicado", "bim_simples", self.su)
        dup.series.add(self.serie5)
        erros, _ = servicos.validar_modelo(dup)
        self.assertTrue(any("Conflito" in e for e in erros))
        with self.assertRaises(ValidationError):
            servicos.publicar_modelo(dup, self.su)

    def test_validacoes_de_publicacao(self):
        vazio = ModeloAvaliacao.objects.create(instituicao=self.a, ano_letivo=self.ano, nome="Vazio")
        self.assertTrue(servicos.validar_modelo(vazio)[0])
        self.m.formula_final = "B1 + XYZ"
        self.m.save()
        self.assertTrue(any("Fórmula da média anual" in e for e in servicos.validar_modelo(self.m)[0]))
        with self.assertRaises(ValidationError):
            servicos.publicar_modelo(self.m, self.su)
        self.m.formula_final = ""
        self.m.media_aprovacao = D("11")
        self.m.save()
        self.assertTrue(servicos.validar_modelo(self.m)[0])

    def test_travas_com_notas_lancadas(self):
        self.publicar()
        a = self.av("A", "ATV", maxima="4")
        servicos.lancar_notas(a, {self.mat[0].pk: D(3)}, self.prof1)
        self.assertTrue(servicos.estrutura_travada(self.m))
        with self.assertRaises(ValidationError):
            servicos.despublicar_modelo(self.m, self.su)
        c = self.suporte()
        r = c.post(reverse("diario:componente_excluir", args=[self.m.pk, self.fase().pk, self.comp("ATV").pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ComponenteNota.objects.filter(pk=self.comp("ATV").pk).exists())
        r = c.post(reverse("diario:fase_excluir", args=[self.m.pk, self.fase().pk]))
        self.assertTrue(PeriodoLetivo.objects.filter(pk=self.fase().pk).exists())
        # parâmetros continuam editáveis (e auditados); a estrutura some do formulário
        r = c.get(reverse("diario:modelo_editar", args=[self.m.pk]))
        self.assertNotContains(r, 'name="tipo_fase"')
        self.assertNotContains(r, 'name="series"')
        self.assertContains(r, 'name="media_aprovacao"')

    def test_despublicar_sem_notas_e_duplicar(self):
        self.publicar()
        servicos.despublicar_modelo(self.m, self.su)
        self.assertEqual(self.m.situacao, "RASCUNHO")
        self.assertIsNone(servicos.modelo_da_alocacao(type(self.aloc).objects.get(pk=self.aloc.pk)))
        ano2 = AnoLetivo.objects.create(instituicao=self.a, nome="2028", ano=2028, data_inicio=date(2028, 2, 1), data_fim=date(2028, 12, 15))
        novo = servicos.duplicar_modelo(self.m, ano2, "Cópia 2028", self.su)
        self.assertEqual(novo.situacao, "RASCUNHO")
        self.assertEqual(novo.fases.count(), 4)
        self.assertEqual(ComponenteNota.objects.filter(fase__modelo=novo).count(), 8)
        self.assertEqual(list(novo.series.all()), [self.serie5])
        self.assertEqual(novo.fases.get(ordem=1).data_inicio.year, 2028)
        with self.assertRaises(ValidationError):
            servicos.duplicar_modelo(self.m, ano2, "Cópia 2028", self.su)

    def test_gerar_fases_por_tipo(self):
        for tipo, n in (("TRIMESTRAL", 3), ("SEMESTRAL", 2), ("MENSAL", 10)):
            m = ModeloAvaliacao.objects.create(instituicao=self.a, ano_letivo=self.ano, nome=tipo, tipo_fase=tipo)
            servicos.gerar_fases(m, self.su)
            fs = list(m.fases.order_by("ordem"))
            self.assertEqual(len(fs), n)
            self.assertEqual((fs[0].data_inicio, fs[-1].data_fim), (self.ano.data_inicio, self.ano.data_fim))
            self.assertTrue(all(fs[i].data_fim < fs[i + 1].data_inicio for i in range(n - 1)))
            with self.assertRaises(ValidationError):
                servicos.gerar_fases(m, self.su)

    def test_fechamento_e_periodos_usam_as_fases_do_modelo(self):
        self.publicar()
        self.assertEqual(servicos.periodo_da_data(self.aloc, date(2027, 3, 10)), self.fase(1))
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(), finalizar=True, por=self.prof1)
        c = self.cli(self.prof1)
        self.assertEqual(c.post(reverse("diario:fechar_periodo", args=[self.aloc.pk, self.p1.pk])).status_code, 404)  # período geral
        self.assertEqual(c.post(reverse("diario:fechar_periodo", args=[self.aloc.pk, self.fase(1).pk])).status_code, 302)
        self.assertEqual(self.cli(self.coord).get(reverse("diario:fechamento")).status_code, 200)
        self.assertContains(self.cli(self.coord).get(reverse("diario:fechamento")), "1º Bimestre")


class TelasETests(AvaliacaoBase):
    def test_somente_administrador_do_sistema_em_modo_suporte(self):
        urls = [reverse("diario:modelos"), reverse("diario:modelo_novo"), reverse("diario:modelo", args=[self.m.pk]),
                reverse("diario:modelo_editar", args=[self.m.pk]), reverse("diario:modelo_simulador", args=[self.m.pk]),
                reverse("diario:modelo_duplicar", args=[self.m.pk]), reverse("diario:fase_nova", args=[self.m.pk])]
        for user in (self.adm, self.coord, self.prof1, self.sec):
            c = self.cli(user)
            for u in urls:
                self.assertEqual(c.get(u).status_code, 403, (user.perfil.login, u))
            for nome in ("modelo_publicar", "modelo_despublicar", "modelo_gerar_fases"):
                self.assertEqual(c.post(reverse(f"diario:{nome}", args=[self.m.pk])).status_code, 403, nome)
        self.assertEqual(Client().get(urls[0]).status_code, 302)  # anônimo vai ao login
        # superusuário sem escola aberta
        c = Client()
        c.force_login(self.su)
        self.assertEqual(c.get(urls[0]).status_code, 403)
        c = self.suporte()
        for u in urls:
            self.assertEqual(c.get(u).status_code, 200, u)

    def test_fluxo_completo_pela_tela(self):
        c = self.suporte()
        r = c.post(reverse("diario:modelos"), {"modelo_pronto": "bim_simples", "ano_letivo": self.ano.pk, "nome": "Via tela"})
        self.assertEqual(r.status_code, 302)
        m = ModeloAvaliacao.objects.get(nome="Via tela")
        r = c.post(reverse("diario:modelo_editar", args=[m.pk]), {
            "nome": "Via tela", "ano_letivo": self.ano.pk, "tipo_fase": "BIMESTRAL", "series": [self.serie5.pk, SerieReferencia.objects.get(codigo="EF1-02").pk],
            "nota_maxima": "10", "media_aprovacao": "5.5", "frequencia_minima": "70", "casas_decimais": "1", "arredondamento": "COMERCIAL",
            "formula_final": "(B1 + B2 + B3 + B4) / 4", "regra_rec_parcial": "MAIOR", "usa_rec_final": "on",
            "formula_rec_final": "", "media_aprovacao_rec_final": ""})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        m.refresh_from_db()
        self.assertEqual((m.media_aprovacao, m.casas_decimais, m.usa_rec_final), (D("5.5"), 1, True))
        self.assertEqual(m.series.count(), 2)
        # fórmula inválida volta com erro
        r = c.post(reverse("diario:modelo_editar", args=[m.pk]), {
            "nome": "Via tela", "ano_letivo": self.ano.pk, "tipo_fase": "BIMESTRAL", "nota_maxima": "10", "media_aprovacao": "6",
            "frequencia_minima": "75", "casas_decimais": "2", "arredondamento": "COMERCIAL", "formula_final": "B1 + XYZ",
            "regra_rec_parcial": "MAIOR"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Variável desconhecida")
        # componente, fase, publicar
        r = c.post(reverse("diario:componente_novo", args=[m.pk, m.fases.get(ordem=1).pk]),
                   {"nome": "Teste", "sigla": "atv", "valor_maximo": "10", "agregacao": "SOMA", "ordem": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ComponenteNota.objects.filter(fase__modelo=m, sigla="ATV").exists())  # sigla em maiúsculas
        r = c.post(reverse("diario:componente_novo", args=[m.pk, m.fases.get(ordem=1).pk]),
                   {"nome": "Repetida", "sigla": "ATV", "valor_maximo": "1", "agregacao": "SOMA", "ordem": "2"})
        self.assertEqual(r.status_code, 200)
        r = c.post(reverse("diario:componente_novo", args=[m.pk, m.fases.get(ordem=1).pk]),
                   {"nome": "Reservada", "sigla": "MF", "valor_maximo": "1", "agregacao": "SOMA", "ordem": "2"})
        self.assertEqual(r.status_code, 200)
        c.post(reverse("diario:modelo_publicar", args=[m.pk]))
        m.refresh_from_db()
        self.assertEqual(m.situacao, "PUBLICADO")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="MODELO_AVALIACAO_PUBLICADO").exists())
        self.assertContains(c.get(reverse("diario:modelo", args=[m.pk])), "Despublicar")

    def test_fase_com_formula_e_ordem_duplicada(self):
        c = self.suporte()
        f = self.fase(1)
        base = {"nome": f.nome, "ordem": 1, "data_inicio": "2027-02-01", "data_fim": "2027-04-20", "peso": "1", "formula": "ATV * 2 + PROVA"}
        self.assertEqual(c.post(reverse("diario:fase_editar", args=[self.m.pk, f.pk]), base).status_code, 302)
        self.assertEqual(c.post(reverse("diario:fase_editar", args=[self.m.pk, f.pk]), {**base, "formula": "ATV + XX"}).status_code, 200)
        self.assertEqual(c.post(reverse("diario:fase_editar", args=[self.m.pk, f.pk]), {**base, "ordem": 2}).status_code, 200)  # ordem 2 já existe

    def test_simulador(self):
        c = self.suporte()
        post = {f"c_{self.comp('ATV').pk}": "3,5", f"c_{self.comp('PROVA').pk}": "4"}
        for o in (2, 3, 4):
            post[f"c_{self.comp('ATV', o).pk}"] = "4"
            post[f"c_{self.comp('PROVA', o).pk}"] = "6"
        post["freq"] = "90"
        r = c.post(reverse("diario:modelo_simulador", args=[self.m.pk]), post)
        self.assertEqual(r.status_code, 200)
        res = r.context["resultado"]
        self.assertEqual(res["situacao"], "Aprovado")
        self.assertEqual(res["medias"][0], D("7.50"))
        self.assertEqual(res["media_anual"], D("9.38"))   # (7,5 + 10 + 10 + 10) / 4 = 9,375 -> 9,38
        self.assertContains(r, "Aprovado")
        r = c.post(reverse("diario:modelo_simulador", args=[self.m.pk]), {**post, "freq": "50"})
        self.assertEqual(r.context["resultado"]["situacao"], "Reprovado por frequência")
        r = c.post(reverse("diario:modelo_simulador", args=[self.m.pk]), {"freq": "abc"})
        self.assertContains(r, "Valor inválido")

    def test_isolamento_entre_escolas(self):
        c = self.suporte(self.b)
        self.assertEqual(c.get(reverse("diario:modelo", args=[self.m.pk])).status_code, 404)
        self.assertEqual(c.post(reverse("diario:modelo_publicar", args=[self.m.pk])).status_code, 404)
        self.assertEqual(c.get(reverse("diario:modelo_simulador", args=[self.m.pk])).status_code, 404)
        self.assertNotContains(c.get(reverse("diario:modelos")), "Bimestral ATV+PROVA")
        # ano letivo de outra escola é recusado
        r = c.post(reverse("diario:modelos"), {"modelo_pronto": "bim_simples", "ano_letivo": self.ano.pk, "nome": "X"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ModeloAvaliacao.objects.filter(instituicao=self.b).exists())
        with self.assertRaises(ValidationError):
            servicos.criar_de_preset(self.b, self.ano, "X", "bim_simples", self.su)

    def test_menu_do_administrador_do_sistema(self):
        self.assertContains(self.suporte().get(reverse("core:dashboard"), follow=True), "Modelos de avaliação")
        self.assertNotContains(self.cli(self.adm).get(reverse("core:dashboard")), "Modelos de avaliação")

    def test_relatorios_e_boletim_com_modelo(self):
        self.publicar()
        a = self.av("A", "ATV", maxima="4")
        servicos.lancar_notas(a, {self.mat[0].pk: D(4)}, self.prof1)
        c = self.cli(self.coord)
        for cod in ("notas-medias", "recuperacao", "fechamento", "baixa-frequencia"):
            r = c.get(reverse("diario:relatorio", args=[cod]))
            self.assertEqual(r.status_code, 200, cod)
            self.assertEqual(c.get(reverse("diario:relatorio", args=[cod]) + "?formato=pdf")["Content-Type"], "application/pdf")
        r = c.get(reverse("diario:boletim", args=[self.mat[0].pk]))
        self.assertContains(r, "Nota mínima")
