from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from apps.academic.models import AnoLetivo, SerieOfertada, SerieReferencia
from apps.audit.models import RegistroAuditoria
from apps.documents.models import DocumentoEmitido, ModeloDocumento
from apps.students.forms import PreMatriculaForm
from apps.students.models import Aluno, AlunoResponsavelFinanceiro, LinkPreMatricula

from .helpers import criar_aluno, criar_ano, criar_instituicao, criar_turma_nao_oficial, criar_turma_oficial, \
    criar_usuario

CPF_A = "529.982.247-25"
CPF_B = "111.444.777-35"


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.inst = criar_instituicao("Colégio Aurora")
        self.sec = criar_usuario(self.inst, "Secretaria")
        self.admin = criar_usuario(self.inst, "Administrador")
        self.turma = criar_turma_oficial(self.inst, nome="1º Ano A")
        self.ano = self.turma.ano_letivo
        self.serie = self.turma.serie_ofertada.serie_referencia
        self.link = LinkPreMatricula.objects.create(instituicao=self.inst)
        self.url = reverse("students:pre_matricula_publica", args=[self.link.token])
        self.anon = Client()

    def dados(self, **kw):
        d = {"ano_letivo": self.ano.pk, "serie": self.serie.pk, "nome": "Pedro Souza", "data_nascimento": "2015-05-10",
             "sexo": "M", "nacionalidade": "Brasileira", "nome_mae": "Ana Souza", "responsavel_legal": "PAIS",
             "resp_tipo": "MAE", "aceite": "on"}
        d.update(kw)
        return d


class FormularioPublicoTests(Base):
    def test_abre_sem_login_e_nao_e_indexavel(self):
        r = self.anon.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Colégio Aurora")
        self.assertIn("noindex", r["X-Robots-Tag"])
        self.assertNotContains(r, "observacoes_internas")
        self.assertNotContains(r, 'name="foto"')
        self.assertNotContains(r, "resp_existente")  # lista de responsáveis da escola nunca é exposta

    def test_link_invalido_desativado_ou_escola_inativa(self):
        self.assertEqual(self.anon.get(reverse("students:pre_matricula_publica", args=["xxxx"])).status_code, 404)
        self.link.ativo = False
        self.link.save()
        self.assertEqual(self.anon.get(self.url).status_code, 404)
        self.assertEqual(self.anon.post(self.url, self.dados()).status_code, 404)
        self.link.ativo = True
        self.link.save()
        self.inst.ativa = False
        self.inst.save()
        self.assertEqual(self.anon.get(self.url).status_code, 404)
        self.assertEqual(Aluno.objects.filter(pre_matricula_online=True).count(), 0)

    def test_envio_cria_aluno_em_pre_matricula_online(self):
        r = self.anon.post(self.url, self.dados(cpf=CPF_A))
        self.assertRedirects(r, reverse("students:pre_matricula_concluida", args=[self.link.token]),
                             fetch_redirect_response=False)
        a = Aluno.objects.get(nome="Pedro Souza")
        self.assertEqual(a.instituicao, self.inst)
        self.assertTrue(a.pre_matricula_online and a.ativo)
        self.assertTrue(a.codigo)
        self.assertIsNone(a.criado_por)
        self.assertEqual((a.serie_pretendida, a.ano_letivo_pretendido), (self.serie, self.ano))
        self.assertIsNotNone(a.pre_matricula_em)
        v = AlunoResponsavelFinanceiro.objects.get(aluno=a)
        self.assertEqual((v.parentesco, v.responsavel.nome), ("MAE", "Ana Souza"))
        self.assertTrue(RegistroAuditoria.objects.filter(acao="PRE_MATRICULA_ONLINE", aluno_id=a.pk).exists())
        ok = self.anon.get(reverse("students:pre_matricula_concluida", args=[self.link.token]))
        self.assertContains(ok, a.codigo)

    def test_cpf_duplicado_bloqueia_com_aviso(self):
        criar_aluno(self.inst, self.sec, nome="Já existe", cpf=CPF_A)
        r = self.anon.post(self.url, self.dados(cpf=CPF_A))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "o CPF do aluno já está cadastrado. Por favor, procure o colégio para formalização "
                               "da matrícula do aluno.")
        self.assertEqual(Aluno.objects.filter(nome="Pedro Souza").count(), 0)

    def test_mesmo_cpf_em_outra_escola_e_permitido(self):
        outra = criar_instituicao("Outra Escola")
        criar_aluno(outra, criar_usuario(outra, "Secretaria"), nome="De outra escola", cpf=CPF_A)
        self.anon.post(self.url, self.dados(cpf=CPF_A))
        self.assertTrue(Aluno.objects.filter(instituicao=self.inst, nome="Pedro Souza").exists())

    def test_exige_aceite_cpf_valido_e_responsavel(self):
        for extra in ({"aceite": ""}, {"cpf": "123.456.789-00"}, {"resp_tipo": ""}):
            d = self.dados(**extra)
            if extra.get("aceite") == "":
                d.pop("aceite")
            r = self.anon.post(self.url, d)
            self.assertEqual(r.status_code, 200, extra)
        self.assertFalse(Aluno.objects.filter(nome="Pedro Souza").exists())

    def test_serie_deve_ser_ofertada_no_ano_e_ano_encerrado_nao_aparece(self):
        outra = SerieReferencia.objects.exclude(pk=self.serie.pk).first()
        r = self.anon.post(self.url, self.dados(serie=outra.pk))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Aluno.objects.filter(nome="Pedro Souza").exists())
        self.ano.situacao = AnoLetivo.Situacao.ENCERRADO
        self.ano.save()
        r = self.anon.get(self.url)
        self.assertContains(r, "não está recebendo pré-matrículas")

    def test_isca_de_robo_nao_grava(self):
        r = self.anon.post(self.url, self.dados(contato_alternativo="http://spam"))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Aluno.objects.filter(nome="Pedro Souza").exists())

    def test_limite_de_envios_por_endereco(self):
        from apps.students import views_prematricula as vp
        for i in range(vp.LIMITE_ENVIOS):
            r = self.anon.post(self.url, self.dados(nome=f"Aluno {i}"))
            self.assertEqual(r.status_code, 302)
        r = self.anon.post(self.url, self.dados(nome="Excedente"))
        self.assertEqual(r.status_code, 429)
        self.assertFalse(Aluno.objects.filter(nome="Excedente").exists())

    def test_envio_nao_vaza_para_outra_escola(self):
        outra = criar_instituicao("Outra")
        self.anon.post(self.url, self.dados())
        self.assertEqual(Aluno.objects.filter(instituicao=outra).count(), 0)


class ConfiguracaoDoLinkTests(Base):
    def setUp(self):
        super().setUp()
        self.c = Client()
        self.c.force_login(self.admin)
        self.cfg = reverse("students:pre_matricula_config")

    def test_tela_mostra_o_link(self):
        r = self.c.get(self.cfg)
        self.assertContains(r, self.url)

    def test_cria_link_na_primeira_visita(self):
        LinkPreMatricula.objects.all().delete()
        self.c.get(self.cfg)
        self.assertTrue(LinkPreMatricula.objects.filter(instituicao=self.inst).exists())

    def test_desativar_ativar_e_renovar(self):
        self.c.post(self.cfg, {"acao": "desativar"})
        self.assertEqual(self.anon.get(self.url).status_code, 404)
        self.c.post(self.cfg, {"acao": "ativar"})
        self.assertEqual(self.anon.get(self.url).status_code, 200)
        antigo = self.link.token
        self.c.post(self.cfg, {"acao": "renovar"})
        self.link.refresh_from_db()
        self.assertNotEqual(self.link.token, antigo)
        self.assertEqual(self.anon.get(self.url).status_code, 404)
        self.assertEqual(self.anon.get(reverse("students:pre_matricula_publica", args=[self.link.token])).status_code,
                         200)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="PRE_MATRICULA_LINK_RENOVADO").exists())

    def test_permissoes(self):
        consulta = Client()
        consulta.force_login(criar_usuario(self.inst, "Consulta"))
        self.assertEqual(consulta.get(self.cfg).status_code, 403)
        self.assertEqual(self.anon.get(self.cfg).status_code, 302)
        sec = Client()
        sec.force_login(self.sec)
        self.assertEqual(sec.get(self.cfg).status_code, 200)

    def test_cada_escola_tem_seu_link(self):
        outra = criar_instituicao("Outra")
        c2 = Client()
        c2.force_login(criar_usuario(outra, "Administrador"))
        c2.get(self.cfg)
        self.assertNotEqual(LinkPreMatricula.objects.get(instituicao=outra).token, self.link.token)


class FluxoDaSecretariaTests(Base):
    def setUp(self):
        super().setUp()
        self.anon.post(self.url, self.dados(cpf=CPF_B))
        self.aluno = Aluno.objects.get(nome="Pedro Souza")
        self.c = Client()
        self.c.force_login(self.sec)

    def test_aparece_na_lista_com_situacao_e_filtro(self):
        r = self.c.get(reverse("students:aluno_lista"))
        self.assertContains(r, "Pré-matrícula online")
        r = self.c.get(reverse("students:aluno_lista"), {"situacao": "pre_matricula"})
        self.assertContains(r, "Pedro Souza")
        criar_aluno(self.inst, self.sec, nome="Aluno comum")
        r = self.c.get(reverse("students:aluno_lista"), {"situacao": "pre_matricula"})
        self.assertNotContains(r, "Aluno comum")

    def test_ficha_e_menu_sinalizam(self):
        r = self.c.get(reverse("students:aluno_ficha", args=[self.aluno.pk]))
        self.assertContains(r, "Pré-matrícula online")
        self.assertContains(r, self.serie.nome)
        self.assertEqual(r.context["pre_matriculas_novas"], 1)

    def matricular(self, turma=None):
        return self.c.post(reverse("enrollments:vincular", args=[self.aluno.pk]),
                           {"turma": (turma or self.turma).pk, "data_ingresso": "2027-02-01"})

    def test_matricular_encerra_a_pre_matricula_e_gera_numero(self):
        r = self.matricular()
        self.assertEqual(r.status_code, 302)
        self.aluno.refresh_from_db()
        self.assertFalse(self.aluno.pre_matricula_online)
        self.assertTrue(self.aluno.matriculas.get().numero)
        self.assertTrue(RegistroAuditoria.objects.filter(acao="PRE_MATRICULA_FORMALIZADA").exists())
        self.assertRedirects(r, f"{reverse('students:aluno_ficha', args=[self.aluno.pk])}?aba=matriculas",
                             fetch_redirect_response=False)

    def test_turma_nao_oficial_nao_formaliza(self):
        nao = criar_turma_nao_oficial(self.inst)
        self.c.post(reverse("enrollments:vincular", args=[self.aluno.pk]),
                    {"turma": nao.pk, "data_ingresso": "2027-03-10"})
        self.aluno.refresh_from_db()
        self.assertTrue(self.aluno.pre_matricula_online)

    def test_turma_desejada_e_sugerida(self):
        r = self.c.get(reverse("enrollments:vincular", args=[self.aluno.pk]), {"tipo": "OFICIAL"})
        self.assertContains(r, "Desejada na pré-matrícula")

    def test_sem_modelos_marcados_mantem_o_fluxo_antigo(self):
        r = self.matricular()
        self.assertNotIn("documentos", r["Location"])


class DocumentosNaMatriculaTests(FluxoDaSecretariaTests):
    def modelo(self, nome, destinatario="ALUNO", marcado=True, ativo=True, inst=None, tipo="COMP_MATRICULA"):
        return ModeloDocumento.objects.create(
            instituicao=inst or self.inst, nome=nome, tipo=tipo, destinatario=destinatario, usar_na_matricula=marcado,
            ativo=ativo, conteudo="<p>[nomeAluno] — matrícula [numeroMatricula]</p>")

    def test_modelo_form_salva_a_marcacao(self):
        self.c.force_login(self.admin)
        r = self.c.post(reverse("documents:modelo_novo"), {
            "nome": "Contrato", "tipo": "CONTRATO_SERVICOS", "destinatario": "RESPONSAVEL", "usar_cabecalho": "on",
            "usar_na_matricula": "on", "ativo": "on", "conteudo": "<p>[nomeResponsavel]</p>"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(ModeloDocumento.objects.get(nome="Contrato").usar_na_matricula)
        self.assertContains(self.c.get(reverse("documents:modelo_lista")), "Na matrícula")

    def test_conclusao_leva_a_tela_de_impressao_somente_com_modelos_marcados(self):
        self.modelo("Comprovante de matrícula")
        self.modelo("Contrato", "RESPONSAVEL", tipo="CONTRATO_SERVICOS")
        self.modelo("Não marcado", marcado=False)
        self.modelo("Inativo", ativo=False)
        outra = criar_instituicao("Outra")
        self.modelo("De outra escola", inst=outra)
        r = self.matricular()
        m = self.aluno.matriculas.get()
        self.assertRedirects(r, reverse("enrollments:documentos", args=[m.pk]), fetch_redirect_response=False)
        pagina = self.c.get(reverse("enrollments:documentos", args=[m.pk]))
        self.assertContains(pagina, "Comprovante de matrícula")
        self.assertContains(pagina, "Contrato")
        for ausente in ("Não marcado", "Inativo", "De outra escola"):
            self.assertNotContains(pagina, ausente)

    def test_gerar_documento_do_aluno_e_do_responsavel(self):
        comp = self.modelo("Comprovante de matrícula")
        contr = self.modelo("Contrato", "RESPONSAVEL", tipo="CONTRATO_SERVICOS")
        self.matricular()
        m = self.aluno.matriculas.get()
        url = reverse("enrollments:documentos", args=[m.pk])
        r = self.c.post(url, {"modelo": comp.pk})
        doc = DocumentoEmitido.objects.get(modelo=comp)
        self.assertEqual(doc.aluno, self.aluno)
        self.assertIn(m.numero, doc.html)
        self.assertIn(f"?voltar={url}", r["Location"])
        self.assertContains(self.c.get(r["Location"]), f'href="{url}"')
        self.c.post(url, {"modelo": contr.pk})
        self.assertEqual(DocumentoEmitido.objects.get(modelo=contr).responsavel.nome, "Ana Souza")

    def test_responsavel_exige_responsavel_financeiro_definido(self):
        contr = self.modelo("Contrato", "RESPONSAVEL", tipo="CONTRATO_SERVICOS")
        self.aluno.responsaveis_financeiros.all().delete()
        self.matricular()
        m = self.aluno.matriculas.get()
        pagina = self.c.get(reverse("enrollments:documentos", args=[m.pk]))
        self.assertContains(pagina, "Defina o responsável financeiro")
        self.c.post(reverse("enrollments:documentos", args=[m.pk]), {"modelo": contr.pk})
        self.assertFalse(DocumentoEmitido.objects.exists())

    def test_nao_emite_modelo_nao_marcado_nem_de_outra_escola(self):
        livre = self.modelo("Não marcado", marcado=False)
        self.matricular()
        m = self.aluno.matriculas.get()
        r = self.c.post(reverse("enrollments:documentos", args=[m.pk]), {"modelo": livre.pk})
        self.assertEqual(r.status_code, 404)

    def test_isolamento_entre_escolas_e_permissao(self):
        self.modelo("Comprovante de matrícula")
        self.matricular()
        m = self.aluno.matriculas.get()
        outra = criar_instituicao("Outra")
        c2 = Client()
        c2.force_login(criar_usuario(outra, "Secretaria"))
        self.assertEqual(c2.get(reverse("enrollments:documentos", args=[m.pk])).status_code, 404)
        coord = Client()
        coord.force_login(criar_usuario(self.inst, "Coordenador"))
        self.assertEqual(coord.get(reverse("enrollments:documentos", args=[m.pk])).status_code, 403)
        self.assertEqual(self.anon.get(reverse("enrollments:documentos", args=[m.pk])).status_code, 302)

    def test_voltar_ignora_endereco_externo(self):
        comp = self.modelo("Comprovante de matrícula")
        self.matricular()
        doc = self.c.post(reverse("enrollments:documentos", args=[self.aluno.matriculas.get().pk]),
                          {"modelo": comp.pk})
        pk = DocumentoEmitido.objects.get().pk
        r = self.c.get(reverse("documents:documento", args=[pk]), {"voltar": "https://evil.example/x"})
        self.assertNotContains(r, "evil.example")


class LimiteAtrasDeProxyTests(Base):
    def test_ip_do_visitante_com_proxy_confiavel(self):
        from django.test import override_settings
        from apps.students import views_prematricula as vp
        with override_settings(PRE_MATRICULA_CONFIAR_PROXY=True):
            for i in range(vp.LIMITE_ENVIOS):
                r = self.anon.post(self.url, self.dados(nome=f"A{i}"), HTTP_X_FORWARDED_FOR="9.9.9.9, 10.0.0.1")
                self.assertEqual(r.status_code, 302)
            r = self.anon.post(self.url, self.dados(nome="B"), HTTP_X_FORWARDED_FOR="8.8.8.8, 10.0.0.1")
            self.assertEqual(r.status_code, 429)  # mesmo visitante (último endereço, visto pelo proxy): limite atingido
            r = self.anon.post(self.url, self.dados(nome="C"), HTTP_X_FORWARDED_FOR="1.1.1.1, 10.0.0.2")
            self.assertEqual(r.status_code, 302)  # outro visitante (outro último endereço): livre
