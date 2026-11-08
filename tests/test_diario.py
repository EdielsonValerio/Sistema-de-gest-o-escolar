"""Diário de Classe: perfis Professor/Coordenador, alocação, aulas, frequência, notas, fechamento, relatórios e isolamento."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.academic.models import AnoLetivo
from apps.audit.models import RegistroAuditoria
from apps.diario import calculos, servicos
from apps.diario.models import (AlocacaoProfessor, Aula, Avaliacao, ConfiguracaoDiario, Disciplina, FechamentoDiario,
                                Frequencia, Nota, PeriodoLetivo, Planejamento)
from apps.enrollments.services import criar_matricula

from .helpers import criar_aluno, criar_ano, criar_instituicao, criar_turma_oficial, criar_usuario

HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@override_settings(PASSWORD_HASHERS=HASH)
class Base(TestCase):
    def setUp(self):
        self.a, self.b = criar_instituicao("Escola A"), criar_instituicao("Escola B")
        self.adm = criar_usuario(self.a, "Administrador")
        self.coord = criar_usuario(self.a, "Coordenador")
        self.prof1 = criar_usuario(self.a, "Professor")
        self.prof2 = criar_usuario(self.a, "Professor")
        self.sec = criar_usuario(self.a, "Secretaria")
        self.prof_b = criar_usuario(self.b, "Professor")
        self.coord_b = criar_usuario(self.b, "Coordenador")
        self.ano = criar_ano(self.a, 2027)
        self.turma = criar_turma_oficial(self.a, self.ano, numero=1, nome="5º Ano A")
        self.turma2 = criar_turma_oficial(self.a, self.ano, codigo_serie="EF1-02", numero=1, nome="Outra Turma")
        self.mat = []
        for i in range(3):
            al = criar_aluno(self.a, self.sec, nome=f"Aluno {i} Diário")
            self.mat.append(criar_matricula(aluno=al, turma=self.turma, usuario=self.sec))
        self.mat_outra = criar_matricula(aluno=criar_aluno(self.a, self.sec, nome="Aluno Outra Turma"),
                                         turma=self.turma2, usuario=self.sec)
        self.disc = Disciplina.objects.create(instituicao=self.a, nome="Matemática")
        self.disc2 = Disciplina.objects.create(instituicao=self.a, nome="Português")
        self.disc_b = Disciplina.objects.create(instituicao=self.b, nome="Matemática")
        self.p1 = PeriodoLetivo.objects.create(instituicao=self.a, ano_letivo=self.ano, nome="1º Bimestre", ordem=1,
                                               data_inicio=date(2027, 2, 1), data_fim=date(2027, 4, 30))
        self.p2 = PeriodoLetivo.objects.create(instituicao=self.a, ano_letivo=self.ano, nome="2º Bimestre", ordem=2,
                                               data_inicio=date(2027, 5, 1), data_fim=date(2027, 7, 31))
        self.aloc = servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma, disciplina=self.disc,
                                    professor=self.prof1, por=self.coord)

    def cli(self, user, escola=None):
        c = Client()
        c.force_login(user)
        s = c.session
        s["escola_id"] = (escola or user.perfil.instituicao).pk
        s.save()
        return c

    def dados_aula(self, **kw):
        return {"data": date(2027, 3, 10), "horario_inicio": None, "horario_fim": None, "quantidade": 2,
                "conteudo": "Frações", "metodologia": "", "atividades": "", "observacoes": "", **kw}

    def presencas(self, **kw):
        return {m.pk: kw.get(str(i), "P") for i, m in enumerate(self.mat)}


class PerfisETests(Base):
    def test_perfis_existem_com_permissoes_minimas(self):
        prof = set(Group.objects.get(name="Professor").permissions.values_list("codename", flat=True))
        self.assertEqual(prof, {"usar_diario_professor"})
        coord = set(Group.objects.get(name="Coordenador").permissions.values_list("codename", flat=True))
        self.assertIn("coordenar_diario", coord)
        self.assertIn("gerenciar_alocacoes", coord)
        for proibido in ("change_aluno", "add_turma", "change_turma", "gerenciar_usuarios", "view_parcela", "registrar_recebimento",
                         "configurar_cobranca", "add_instituicao", "change_instituicao"):
            self.assertNotIn(proibido, coord | prof)

    def test_usuario_professor_e_coordenador_criados_pelo_fluxo_existente(self):
        c = self.cli(self.adm)
        for grupo in ("Professor", "Coordenador"):
            r = c.post(reverse("accounts:usuario_novo"), {
                "username": f"u-{grupo}", "first_name": "N", "last_name": "", "email": "", "perfil": Group.objects.get(name=grupo).pk,
                "is_active": "on", "senha1": "Provisoria#2026", "senha2": "Provisoria#2026"})
            self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
            u = User.objects.get(perfil__login=f"u-{grupo}", perfil__instituicao=self.a)
            self.assertTrue(u.perfil.deve_trocar_senha)  # senha provisória / primeiro acesso
            self.assertTrue(u.groups.filter(name=grupo).exists())

    def test_login_leva_cada_perfil_ao_seu_painel(self):
        self.assertRedirects(self.cli(self.prof1).get(reverse("core:dashboard")), reverse("diario:painel"),
                             fetch_redirect_response=False)
        self.assertRedirects(self.cli(self.coord).get(reverse("core:dashboard")), reverse("diario:coordenacao"),
                             fetch_redirect_response=False)
        self.assertEqual(self.cli(self.adm).get(reverse("core:dashboard")).status_code, 200)

    def test_menu_por_perfil(self):
        p = self.cli(self.prof1).get(reverse("diario:painel"))
        self.assertContains(p, "Meu diário")
        self.assertNotContains(p, reverse("students:aluno_lista"))
        self.assertNotContains(p, reverse("diario:alocacoes"))
        c = self.cli(self.coord).get(reverse("diario:coordenacao"))
        self.assertContains(c, reverse("diario:alocacoes"))
        self.assertNotContains(c, reverse("finance:caixa"))


class RestricoesDoProfessorTests(Base):
    def test_professor_nao_acessa_areas_gerais(self):
        c = self.cli(self.prof1)
        for url in (reverse("students:aluno_lista"), reverse("academic:turma_lista"), reverse("accounts:usuario_lista"),
                    reverse("finance:caixa"), reverse("enrollments:lista"), reverse("billing:configuracao"),
                    reverse("diario:alocacoes"), reverse("diario:alocacao_nova"), reverse("diario:coordenacao"),
                    reverse("diario:fechamento"), reverse("diario:disciplinas"), reverse("diario:aulas_coordenacao"),
                    reverse("diario:planejamentos_coordenacao"), reverse("diario:criterios")):
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertEqual(c.get(reverse("core:escola_lista")).status_code, 403)

    def test_professor_so_ve_as_proprias_alocacoes(self):
        outra = servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma, disciplina=self.disc2, professor=self.prof2,
                                por=self.coord)
        c = self.cli(self.prof1)
        painel = c.get(reverse("diario:painel"))
        self.assertContains(painel, "Matemática")
        self.assertNotContains(painel, "Português")
        for nome in ("diario", "aulas", "aula_nova", "frequencia", "avaliacoes", "avaliacao_nova", "desempenho",
                     "planejamentos", "planejamento_novo"):
            self.assertEqual(c.get(reverse(f"diario:{nome}", args=[outra.pk])).status_code, 404, nome)
        self.assertEqual(c.post(reverse("diario:aula_nova", args=[outra.pk]), {}).status_code, 404)

    def test_professor_de_outra_escola_nao_acessa(self):
        c = self.cli(self.prof_b)
        self.assertEqual(c.get(reverse("diario:diario", args=[self.aloc.pk])).status_code, 404)
        self.assertNotContains(c.get(reverse("diario:painel")), "5º Ano A")

    def test_alocacao_encerrada_nao_aceita_lancamentos(self):
        servicos.encerrar_alocacao(self.aloc, self.coord)
        with self.assertRaises(ValidationError):
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas={}, finalizar=False, por=self.prof1)
        self.assertEqual(self.cli(self.prof1).get(reverse("diario:aulas", args=[self.aloc.pk])).status_code, 200)  # consulta ok

    def test_usuario_inativo_nao_entra_mas_historico_permanece(self):
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(), finalizar=True,
                             por=self.prof1)
        self.prof1.is_active = False
        self.prof1.save()
        self.assertEqual(Client().login(username=self.prof1.username, password="senha-de-teste-123"), False)
        self.assertEqual(Aula.objects.filter(professor=self.prof1).count(), 1)


class AlocacaoTests(Base):
    def test_coordenador_aloca_pela_tela(self):
        c = self.cli(self.coord)
        r = c.post(reverse("diario:alocacao_nova"), {"ano_letivo": self.ano.pk, "turma": self.turma.pk,
                                                    "disciplina": self.disc2.pk, "professor": self.prof2.pk})
        self.assertRedirects(r, reverse("diario:alocacoes"), fetch_redirect_response=False)
        self.assertTrue(AlocacaoProfessor.objects.filter(professor=self.prof2, disciplina=self.disc2, situacao="ATIVA").exists())
        self.assertTrue(RegistroAuditoria.objects.filter(acao="ALOCACAO_CRIADA", instituicao=self.a).exists())
        self.assertContains(c.get(reverse("diario:alocacoes")), "Português")

    def test_regras_da_alocacao(self):
        e, ano, t = self.a, self.ano, self.turma
        # duplicada
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano, turma=t, disciplina=self.disc, professor=self.prof1, por=self.coord)
        # só professor ativo com perfil Professor
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano, turma=t, disciplina=self.disc2, professor=self.sec, por=self.coord)
        self.prof2.is_active = False
        self.prof2.save()
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano, turma=t, disciplina=self.disc2, professor=self.prof2, por=self.coord)
        # entre escolas
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano, turma=t, disciplina=self.disc2, professor=self.prof_b, por=self.coord)
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano, turma=t, disciplina=self.disc_b, professor=self.prof1, por=self.coord)
        # turma de outro ano letivo
        ano2 = AnoLetivo.objects.create(instituicao=e, nome="2028", ano=2028, data_inicio=date(2028, 2, 1), data_fim=date(2028, 12, 1))
        with self.assertRaises(ValidationError):
            servicos.alocar(escola=e, ano=ano2, turma=t, disciplina=self.disc2, professor=self.prof1, por=self.coord)

    def test_professor_em_varias_turmas_e_turma_com_varios_professores(self):
        servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma2, disciplina=self.disc, professor=self.prof1, por=self.coord)
        servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma, disciplina=self.disc2, professor=self.prof2, por=self.coord)
        self.assertEqual(AlocacaoProfessor.objects.filter(professor=self.prof1, situacao="ATIVA").count(), 2)
        self.assertEqual(AlocacaoProfessor.objects.filter(turma=self.turma, situacao="ATIVA").count(), 2)

    def test_trocar_e_encerrar_preservam_historico(self):
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(), finalizar=True,
                             por=self.prof1)
        c = self.cli(self.coord)
        c.post(reverse("diario:alocacao_trocar", args=[self.aloc.pk]), {"professor": self.prof2.pk})
        self.aloc.refresh_from_db()
        self.assertEqual(self.aloc.situacao, "ENCERRADA")
        self.assertIsNotNone(self.aloc.data_fim)
        nova = AlocacaoProfessor.objects.get(professor=self.prof2, situacao="ATIVA")
        self.assertEqual(Aula.objects.filter(alocacao=self.aloc, professor=self.prof1).count(), 1)  # histórico intacto
        self.assertEqual(Aula.objects.filter(alocacao=nova).count(), 0)
        # o antigo professor perde a turma ativa; o novo a recebe
        self.assertNotContains(self.cli(self.prof1).get(reverse("diario:painel")), "5º Ano A")
        self.assertContains(self.cli(self.prof2).get(reverse("diario:painel")), "5º Ano A")
        c.post(reverse("diario:alocacao_encerrar", args=[nova.pk]))
        nova.refresh_from_db()
        self.assertEqual(nova.situacao, "ENCERRADA")

    def test_coordenador_nao_mexe_em_alocacao_de_outra_escola(self):
        c = self.cli(self.coord_b)
        self.assertEqual(c.post(reverse("diario:alocacao_encerrar", args=[self.aloc.pk])).status_code, 404)
        self.assertEqual(c.get(reverse("diario:alocacao_trocar", args=[self.aloc.pk])).status_code, 404)
        r = c.post(reverse("diario:alocacao_nova"), {"ano_letivo": self.ano.pk, "turma": self.turma.pk,
                                                    "disciplina": self.disc_b.pk, "professor": self.prof_b.pk})
        self.assertEqual(r.status_code, 200)  # campos de outra escola são inválidos
        self.assertFalse(AlocacaoProfessor.objects.filter(instituicao=self.b).exists())
        self.assertNotContains(c.get(reverse("diario:alocacoes")), "5º Ano A")

    def test_secretaria_nao_aloca(self):
        c = self.cli(self.sec)
        self.assertEqual(c.get(reverse("diario:alocacao_nova")).status_code, 403)
        self.assertEqual(c.get(reverse("diario:alocacoes")).status_code, 403)

    def test_administrador_tambem_aloca(self):
        self.assertEqual(self.cli(self.adm).get(reverse("diario:alocacao_nova")).status_code, 200)


class AulasFrequenciaTests(Base):
    def test_rascunho_e_finalizar_pela_tela(self):
        c = self.cli(self.prof1)
        url = reverse("diario:aula_nova", args=[self.aloc.pk])
        form = {"data": "2027-03-10", "quantidade": 2, "conteudo": "Frações", "horario_inicio": "", "horario_fim": "",
                "metodologia": "", "atividades": "", "observacoes": ""}
        pagina = c.get(url)
        self.assertContains(pagina, "Aluno 0 Diário")
        self.assertNotContains(pagina, "Aluno Outra Turma")  # só alunos da turma (matrículas ativas)
        r = c.post(url, {**form, "acao": "rascunho"})
        self.assertEqual(r.status_code, 302)
        aula = Aula.objects.get()
        self.assertEqual((aula.status, aula.periodo), ("RASCUNHO", self.p1))
        # finalizar sem frequência completa é recusado
        r = c.post(reverse("diario:aula_editar", args=[self.aloc.pk, aula.pk]), {**form, "acao": "finalizar"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "frequência de todos")
        post = {**form, "acao": "finalizar", f"freq_{self.mat[0].pk}": "P", f"freq_{self.mat[1].pk}": "F",
                f"freq_{self.mat[2].pk}": "J"}
        r = c.post(reverse("diario:aula_editar", args=[self.aloc.pk, aula.pk]), post)
        self.assertEqual(r.status_code, 302)
        aula.refresh_from_db()
        self.assertEqual(aula.status, "FINALIZADA")
        self.assertEqual(dict(Frequencia.objects.values_list("matricula_id", "situacao")),
                         {self.mat[0].pk: "P", self.mat[1].pk: "F", self.mat[2].pk: "J"})
        self.assertTrue(RegistroAuditoria.objects.filter(acao="AULA_FINALIZADA").exists())
        # finalizada: não edita
        r = c.post(reverse("diario:aula_editar", args=[self.aloc.pk, aula.pk]), {**post, "conteudo": "Outro"})
        self.assertEqual(r.status_code, 302)
        aula.refresh_from_db()
        self.assertEqual(aula.conteudo, "Frações")

    def test_conteudo_obrigatorio_para_finalizar(self):
        with self.assertRaises(ValidationError):
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(conteudo=" "), presencas=self.presencas(),
                                 finalizar=True, por=self.prof1)

    def test_nao_aceita_aluno_de_fora_da_turma(self):
        with self.assertRaises(ValidationError):
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(),
                                 presencas={**self.presencas(), self.mat_outra.pk: "P"}, finalizar=False, por=self.prof1)
        self.assertEqual(Aula.objects.count(), 0)  # transação desfeita

    def test_frequencia_percentual_e_baixa_frequencia(self):
        # 3 aulas de 2 horas: aluno0 sempre presente, aluno1 falta 2, aluno2 falta justificada (abonada)
        for dia, sit1, sit2 in ((10, "P", "P"), (11, "F", "J"), (12, "F", "J")):
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(data=date(2027, 3, dia)),
                                 presencas={self.mat[0].pk: "P", self.mat[1].pk: sit1, self.mat[2].pk: sit2},
                                 finalizar=True, por=self.prof1)
        freq = calculos.frequencia_da_alocacao(self.aloc)
        self.assertEqual(freq[self.mat[0].pk], (Decimal("100.00"), 6, 0))
        self.assertEqual(freq[self.mat[1].pk], (Decimal("33.33"), 6, 4))
        self.assertEqual(freq[self.mat[2].pk], (Decimal("100.00"), 6, 0))
        c = self.cli(self.prof1)
        self.assertContains(c.get(reverse("diario:frequencia", args=[self.aloc.pk])), "33,33%")
        _, linhas = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual([l["baixa_frequencia"] for l in linhas], [False, True, False])

    def test_rascunho_nao_conta_na_frequencia(self):
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(), finalizar=False,
                             por=self.prof1)
        self.assertEqual(calculos.frequencia_da_alocacao(self.aloc), {})

    def test_correcao_solicitada_pela_coordenacao(self):
        aula = servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(),
                                    finalizar=True, por=self.prof1)
        c = self.cli(self.coord)
        r = c.post(reverse("diario:solicitar_correcao", args=[aula.pk]), {"motivo": "Conteúdo incorreto"})
        self.assertEqual(r.status_code, 302)
        aula.refresh_from_db()
        self.assertEqual(aula.status, "CORRECAO")
        self.assertContains(self.cli(self.prof1).get(reverse("diario:aula_editar", args=[self.aloc.pk, aula.pk])), "Conteúdo incorreto")
        servicos.salvar_aula(alocacao=self.aloc, aula=aula, dados=self.dados_aula(conteudo="Corrigido"), presencas=self.presencas(),
                             finalizar=True, por=self.prof1)
        aula.refresh_from_db()
        self.assertEqual((aula.status, aula.conteudo), ("FINALIZADA", "Corrigido"))
        # sem motivo: recusa
        with self.assertRaises(ValidationError):
            servicos.solicitar_correcao(aula, "  ", self.coord)

    def test_professor_nao_solicita_correcao_nem_coordenador_de_outra_escola(self):
        aula = servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(),
                                    finalizar=True, por=self.prof1)
        self.assertEqual(self.cli(self.prof1).post(reverse("diario:solicitar_correcao", args=[aula.pk]), {"motivo": "x"}).status_code, 403)
        self.assertEqual(self.cli(self.coord_b).post(reverse("diario:solicitar_correcao", args=[aula.pk]), {"motivo": "x"}).status_code, 404)


class NotasTests(Base):
    def av(self, titulo="Prova", periodo=None, tipo="NORMAL", maxima="10", peso="1"):
        return servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={
            "periodo": periodo or self.p1, "titulo": titulo, "tipo": tipo, "data": date(2027, 3, 20),
            "nota_maxima": Decimal(maxima), "peso": Decimal(peso)})

    def test_lancar_notas_pela_tela_e_validacoes(self):
        av = self.av()
        c = self.cli(self.prof1)
        url = reverse("diario:notas", args=[self.aloc.pk, av.pk])
        self.assertContains(c.get(url), "Aluno 0 Diário")
        r = c.post(url, {f"nota_{self.mat[0].pk}": "8,5", f"nota_{self.mat[1].pk}": "4", f"nota_{self.mat[2].pk}": ""})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Nota.objects.get(avaliacao=av, matricula=self.mat[0]).valor, Decimal("8.50"))
        self.assertIsNone(Nota.objects.get(avaliacao=av, matricula=self.mat[2]).valor)
        for ruim in ("11", "-1", "abc"):
            r = c.post(url, {f"nota_{self.mat[0].pk}": ruim})
            self.assertEqual(r.status_code, 200, ruim)
        self.assertEqual(Nota.objects.get(avaliacao=av, matricula=self.mat[0]).valor, Decimal("8.50"))

    def test_nota_de_aluno_de_fora_da_turma_e_recusada(self):
        av = self.av()
        with self.assertRaises(ValidationError):
            servicos.lancar_notas(av, {self.mat_outra.pk: Decimal("5")}, self.prof1)

    def test_medias_ponderadas_e_recuperacao(self):
        a1, a2 = self.av("P1", peso="1"), self.av("P2", peso="3", maxima="20")
        servicos.lancar_notas(a1, {self.mat[0].pk: Decimal("8"), self.mat[1].pk: Decimal("4")}, self.prof1)
        servicos.lancar_notas(a2, {self.mat[0].pk: Decimal("10"), self.mat[1].pk: Decimal("4")}, self.prof1)  # 10/20 = 5,0
        _, linhas = calculos.desempenho_da_alocacao(self.aloc)
        por = {l["matricula"].pk: l for l in linhas}
        # aluno0: (8×1 + 5×3)/4 = 5,75 ; aluno1: (4×1 + 2×3)/4 = 2,5
        self.assertEqual(por[self.mat[0].pk]["medias"][0], Decimal("5.75"))
        self.assertEqual(por[self.mat[1].pk]["medias"][0], Decimal("2.50"))
        rec = self.av("Rec", tipo="RECUPERACAO")
        servicos.lancar_notas(rec, {self.mat[1].pk: Decimal("7")}, self.prof1)
        _, linhas = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual({l["matricula"].pk: l for l in linhas}[self.mat[1].pk]["medias"][0], Decimal("7.00"))  # substitui (maior)
        # recuperação menor não reduz
        servicos.lancar_notas(rec, {self.mat[1].pk: Decimal("1")}, self.prof1)
        _, linhas = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual({l["matricula"].pk: l for l in linhas}[self.mat[1].pk]["medias"][0], Decimal("2.50"))

    def test_criterios_configuraveis(self):
        a1 = self.av()
        servicos.lancar_notas(a1, {self.mat[0].pk: Decimal("6")}, self.prof1)
        _, l = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual(l[0]["situacao"], "Aprovado")
        cfg = servicos.configuracao(self.a)
        cfg.media_aprovacao = Decimal("7")
        cfg.save()
        _, l = calculos.desempenho_da_alocacao(self.aloc)
        self.assertEqual(l[0]["situacao"], "Abaixo da média")
        r = self.cli(self.coord).post(reverse("diario:criterios"), {"media_aprovacao": "5", "frequencia_minima": "70",
                                                                      "usa_recuperacao": "on", "nota_maxima_padrao": "10"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ConfiguracaoDiario.objects.get(instituicao=self.a).media_aprovacao, Decimal("5.00"))
        self.assertEqual(self.cli(self.prof1).get(reverse("diario:criterios")).status_code, 403)

    def test_periodo_de_outro_ano_e_invalido(self):
        outro = AnoLetivo.objects.create(instituicao=self.a, nome="2028", ano=2028, data_inicio=date(2028, 2, 1), data_fim=date(2028, 12, 1))
        p = PeriodoLetivo.objects.create(instituicao=self.a, ano_letivo=outro, nome="X", ordem=1, data_inicio=date(2028, 2, 1),
                                         data_fim=date(2028, 4, 1))
        with self.assertRaises(ValidationError):
            self.av(periodo=p)


class FechamentoTests(Base):
    def test_fechar_bloqueia_e_coordenacao_reabre(self):
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(), finalizar=True,
                             por=self.prof1)
        c = self.cli(self.prof1)
        r = c.post(reverse("diario:fechar_periodo", args=[self.aloc.pk, self.p1.pk]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(FechamentoDiario.objects.get(alocacao=self.aloc, periodo=self.p1).fechado)
        with self.assertRaises(ValidationError):
            servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(data=date(2027, 3, 15)), presencas={},
                                 finalizar=False, por=self.prof1)
        with self.assertRaises(ValidationError):
            servicos.criar_avaliacao(alocacao=self.aloc, por=self.prof1, dados={"periodo": self.p1, "titulo": "x", "tipo": "NORMAL",
                                     "data": date(2027, 3, 1), "nota_maxima": Decimal(10), "peso": Decimal(1)})
        # outro período segue aberto
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(data=date(2027, 6, 2)), presencas={}, finalizar=False,
                             por=self.prof1)
        f = FechamentoDiario.objects.get(alocacao=self.aloc, periodo=self.p1)
        # professor não reabre; coordenação precisa de motivo
        self.assertEqual(c.post(reverse("diario:reabrir", args=[f.pk]), {"motivo": "x"}).status_code, 403)
        cc = self.cli(self.coord)
        cc.post(reverse("diario:reabrir", args=[f.pk]), {"motivo": ""})
        f.refresh_from_db()
        self.assertTrue(f.fechado)
        cc.post(reverse("diario:reabrir", args=[f.pk]), {"motivo": "Erro de lançamento"})
        f.refresh_from_db()
        self.assertFalse(f.fechado)
        self.assertEqual(f.reaberto_por, self.coord)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="DIARIO_REABERTO").exists())

    def test_nao_fecha_com_rascunho(self):
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas={}, finalizar=False, por=self.prof1)
        with self.assertRaises(ValidationError):
            servicos.fechar_diario(self.aloc, self.p1, self.prof1)

    def test_nao_fecha_periodo_de_outra_escola(self):
        c = self.cli(self.coord_b)
        self.assertEqual(c.post(reverse("diario:reabrir", args=[999999]), {"motivo": "x"}).status_code, 404)


class PlanejamentoTests(Base):
    def test_planejamento_e_comparacao_previsto_x_ministrado(self):
        c = self.cli(self.prof1)
        r = c.post(reverse("diario:planejamento_novo", args=[self.aloc.pk]), {
            "tipo": "ANUAL", "periodo": "", "conteudos_previstos": "Frações\nDecimais", "objetivos": "Operar", "metodologias": "",
            "habilidades": "", "bncc": "EF05MA03"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(conteudo="Frações simples"),
                             presencas=self.presencas(), finalizar=True, por=self.prof1)
        pagina = c.get(reverse("diario:planejamentos", args=[self.aloc.pk]))
        self.assertContains(pagina, "Frações simples")
        self.assertContains(pagina, "EF05MA03")
        # período obrigatório quando o tipo é "por período"
        r = c.post(reverse("diario:planejamento_novo", args=[self.aloc.pk]), {"tipo": "PERIODO", "periodo": "", "conteudos_previstos": "x"})
        self.assertEqual(r.status_code, 200)
        # coordenação consulta; secretaria não
        self.assertContains(self.cli(self.coord).get(reverse("diario:planejamentos_coordenacao")), "Frações")
        self.assertEqual(self.cli(self.sec).get(reverse("diario:planejamentos_coordenacao")).status_code, 403)
        self.assertNotContains(self.cli(self.coord_b).get(reverse("diario:planejamentos_coordenacao")), "Frações")


class PaineisERelatoriosTests(Base):
    def setUp(self):
        super().setUp()
        servicos.salvar_aula(alocacao=self.aloc, aula=None, dados=self.dados_aula(), presencas=self.presencas(**{"1": "F"}),
                             finalizar=True, por=self.prof1)

    def test_painel_do_professor(self):
        r = self.cli(self.prof1).get(reverse("diario:painel"))
        self.assertContains(r, "5º Ano A")
        self.assertContains(r, "Matemática")

    def test_painel_da_coordenacao(self):
        r = self.cli(self.coord).get(reverse("diario:coordenacao"))
        self.assertEqual(r.status_code, 200)
        ctx = r.context
        self.assertEqual(ctx["professores"], 2)
        self.assertEqual(ctx["turmas_com"], 1)
        self.assertEqual(ctx["turmas_sem"], 1)  # "Outra Turma"
        self.assertContains(r, "Alocar professor")
        # outra escola não enxerga estes números
        self.assertEqual(self.cli(self.coord_b).get(reverse("diario:coordenacao")).context["turmas_com"], 0)

    def test_pendencias_listam_turma_sem_professor(self):
        r = self.cli(self.coord).get(reverse("diario:pendencias"))
        self.assertContains(r, "Outra Turma")

    def test_telas_da_coordenacao_abrem(self):
        c = self.cli(self.coord)
        for nome in ("alocacoes", "alocacao_nova", "disciplinas", "disciplina_nova", "periodos", "periodo_novo", "criterios",
                     "aulas_coordenacao", "pendencias", "fechamento", "relatorios", "planejamentos_coordenacao"):
            self.assertEqual(c.get(reverse(f"diario:{nome}")).status_code, 200, nome)
        c.post(reverse("diario:disciplina_nova"), {"nome": "História", "sigla": "HIS", "ativa": "on"})
        self.assertTrue(Disciplina.objects.filter(instituicao=self.a, nome="História").exists())
        self.assertEqual(self.cli(self.prof1).get(reverse("diario:disciplina_nova")).status_code, 403)

    def test_bimestres_automaticos(self):
        ano = AnoLetivo.objects.create(instituicao=self.a, nome="2029", ano=2029, data_inicio=date(2029, 2, 1), data_fim=date(2029, 11, 30))
        self.cli(self.coord).post(reverse("diario:criar_bimestres", args=[ano.pk]))
        ps = list(PeriodoLetivo.objects.filter(ano_letivo=ano).order_by("ordem"))
        self.assertEqual(len(ps), 4)
        self.assertEqual((ps[0].data_inicio, ps[-1].data_fim), (ano.data_inicio, ano.data_fim))
        self.assertTrue(all(ps[i].data_fim < ps[i + 1].data_inicio for i in range(3)))

    def test_relatorios_tela_e_pdf(self):
        c = self.cli(self.coord)
        for cod in ("aulas", "frequencia-turma", "frequencia-individual", "baixa-frequencia", "notas-medias", "recuperacao",
                    "conteudos", "desempenho-professores", "pendencias", "fechamento"):
            r = c.get(reverse("diario:relatorio", args=[cod]))
            self.assertEqual(r.status_code, 200, cod)
            pdf = c.get(reverse("diario:relatorio", args=[cod]) + "?formato=pdf")
            self.assertEqual(pdf.status_code, 200, cod)
            self.assertEqual(pdf["Content-Type"], "application/pdf")
            self.assertTrue(pdf.content.startswith(b"%PDF"), cod)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="RELATORIO_DIARIO_PDF").exists())
        self.assertContains(c.get(reverse("diario:relatorio", args=["baixa-frequencia"])), "Aluno 1 Diário")

    def test_boletim(self):
        c = self.cli(self.coord)
        r = c.get(reverse("diario:boletim", args=[self.mat[0].pk]))
        self.assertContains(r, "Boletim")
        self.assertContains(r, "Matemática")
        pdf = c.get(reverse("diario:boletim", args=[self.mat[0].pk]) + "?formato=pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        self.assertEqual(self.cli(self.prof1).get(reverse("diario:boletim", args=[self.mat[0].pk])).status_code, 403)
        self.assertEqual(self.cli(self.coord_b).get(reverse("diario:boletim", args=[self.mat[0].pk])).status_code, 404)

    def test_professor_so_ve_relatorios_proprios(self):
        servicos.alocar(escola=self.a, ano=self.ano, turma=self.turma2, disciplina=self.disc2, professor=self.prof2, por=self.coord)
        servicos.salvar_aula(alocacao=AlocacaoProfessor.objects.get(professor=self.prof2), aula=None,
                             dados=self.dados_aula(conteudo="Conteúdo do outro professor"),
                             presencas={self.mat_outra.pk: "P"}, finalizar=True, por=self.prof2)
        c = self.cli(self.prof1)
        r = c.get(reverse("diario:relatorio", args=["conteudos"]))
        self.assertContains(r, "Frações")
        self.assertNotContains(r, "Conteúdo do outro professor")
        self.assertNotContains(c.get(reverse("diario:relatorio", args=["frequencia-individual"])), "Aluno Outra Turma")
        # relatórios gerenciais: só coordenação
        for cod in ("desempenho-professores", "pendencias", "fechamento"):
            self.assertEqual(c.get(reverse("diario:relatorio", args=[cod])).status_code, 404, cod)
        self.assertEqual(c.get(reverse("diario:relatorio", args=["nao-existe"])).status_code, 404)
        # professor tentando filtrar por outro professor continua restrito
        r = c.get(reverse("diario:relatorio", args=["conteudos"]) + f"?professor={self.prof2.pk}")
        self.assertNotContains(r, "Conteúdo do outro professor")

    def test_relatorios_isolados_por_escola(self):
        r = self.cli(self.coord_b).get(reverse("diario:relatorio", args=["conteudos"]))
        self.assertNotContains(r, "Frações")
        self.assertNotContains(self.cli(self.coord_b).get(reverse("diario:relatorio", args=["frequencia-individual"])), "Aluno 0 Diário")

    def test_secretaria_sem_acesso_aos_relatorios_do_diario(self):
        self.assertEqual(self.cli(self.sec).get(reverse("diario:relatorios")).status_code, 403)


class SuperadminTests(Base):
    def test_superadmin_em_modo_suporte_acessa_o_diario_da_escola(self):
        su = User.objects.create_superuser("root", "r@x.com", "senha-root-123")
        c = Client()
        c.force_login(su)
        s = c.session
        s["escola_id"] = 0
        s.save()
        self.assertEqual(c.get(reverse("diario:coordenacao")).status_code, 403)  # sem escola aberta
        c.post(reverse("core:escola_acessar", args=[self.a.pk]))
        self.assertEqual(c.get(reverse("diario:coordenacao")).status_code, 200)
        self.assertContains(c.get(reverse("diario:alocacoes")), "5º Ano A")
        c.post(reverse("core:escola_acessar", args=[self.b.pk]))
        self.assertNotContains(c.get(reverse("diario:alocacoes")), "5º Ano A")


class IdsGrandesTests(Base):
    """Com USE_THOUSAND_SEPARATOR, IDs > 999 não podem sair como '900.001' em valores de formulário."""

    def test_ids_acima_de_mil_nos_formularios(self):
        from apps.core.models import Instituicao
        grande = Instituicao.objects.create(pk=900001, nome="Escola ID Grande")
        u = criar_usuario(grande, "Secretaria")
        r = Client().get(reverse("accounts:login"))
        self.assertContains(r, 'value="900001"')
        self.assertNotContains(r, "900.001")
        r = Client().post(reverse("accounts:login"), {"escola": 900001, "usuario": u.perfil.login or u.username,
                                                      "senha": "senha-de-teste-123"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)

    def test_nome_dos_campos_da_chamada_sem_separador(self):
        from django.db import connection
        # força matrícula com pk grande simulando a renderização
        from django.template import Context, Template
        out = Template("{% load l10n %}freq_{{ m.pk|unlocalize }}").render(Context({"m": type("M", (), {"pk": 1234567})()}))
        self.assertEqual(out, "freq_1234567")
        pagina = self.cli(self.prof1).get(reverse("diario:aula_nova", args=[self.aloc.pk]))
        self.assertContains(pagina, f"freq_{self.mat[0].pk}")
