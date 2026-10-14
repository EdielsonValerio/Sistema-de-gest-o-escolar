import io

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.permissions import sincronizar_perfis
from apps.audit.models import RegistroAuditoria
from apps.documents.models import CabecalhoDocumento, DocumentoEmitido, ModeloDocumento
from apps.documents.services import emitir_documento
from apps.documents.tipos import TIPOS_DOCUMENTO
from apps.documents.variaveis import (CATALOGO, montar_contexto, renderizar, sanitizar_html,
                                      variaveis_desconhecidas)
from apps.students.models import AlunoResponsavelFinanceiro, Responsavel

from .helpers import criar_aluno, criar_instituicao, criar_usuario


def modelo(inst, nome="Declaração de matrícula", destinatario="ALUNO", conteudo=None, **kw):
    return ModeloDocumento.objects.create(
        instituicao=inst, nome=nome, tipo=kw.pop("tipo", "DECL_MATRICULA"), destinatario=destinatario,
        conteudo=conteudo or "<p>Declaramos que [nomeAluno] (código [codigoUnicoAluno]).</p>", **kw)


def vincular(aluno, inst, nome="Ana Responsável"):
    r = Responsavel.objects.create(instituicao=inst, nome=nome, documento="")
    AlunoResponsavelFinanceiro.objects.create(aluno=aluno, responsavel=r, parentesco="MAE")
    return r


class VariaveisTests(TestCase):
    def test_lista_predefinida_de_tipos(self):
        codigos = [c for c, _ in TIPOS_DOCUMENTO]
        self.assertEqual(len(codigos), len(set(codigos)))
        rotulos = [r for _, r in TIPOS_DOCUMENTO]
        self.assertIn("Declaração de matrícula", rotulos)
        self.assertIn("Resposta formal a solicitações dos responsáveis", rotulos)
        self.assertEqual(len(rotulos), len(set(rotulos)))

    def test_substituicao_escapa_valores_e_ignora_caixa(self):
        out = renderizar("<p>[NOMEALUNO] e [naoExiste]</p>", {"nomeAluno": "<b>X</b> & Y"})
        self.assertEqual(out, "<p>&lt;b&gt;X&lt;/b&gt; &amp; Y e [naoExiste]</p>")

    def test_variaveis_desconhecidas(self):
        self.assertEqual(variaveis_desconhecidas("[nomeAluno] [abc] [Xyz]"), ["Xyz", "abc"])
        self.assertEqual(variaveis_desconhecidas("[matriculaUnica] [matriculaunica]"), [])

    def test_sanitizador_remove_scripts_eventos_e_estilos(self):
        sujo = ('<p style="text-align:center;background:url(x)" onclick="x()">Oi<script>alert(1)</script></p>'
                '<img src=x onerror=alert(1)><a href="javascript:x">l</a><iframe src=x></iframe><b>n</b>')
        limpo = sanitizar_html(sujo)
        for proibido in ("script", "onclick", "onerror", "<img", "<a ", "iframe", "javascript", "background"):
            self.assertNotIn(proibido, limpo)
        self.assertIn('<p style="text-align: center">Oi</p>', limpo)
        self.assertIn("<b>n</b>", limpo)

    def test_sanitizador_fecha_tags_abertas(self):
        self.assertEqual(sanitizar_html("<p>a<b>b"), "<p>a<b>b</b></p>")

    def test_todas_as_variaveis_do_catalogo_tem_valor_no_contexto(self):
        inst = criar_instituicao()
        u = criar_usuario(inst, superuser=True)
        a = criar_aluno(inst, u)
        r = vincular(a, inst)
        ctx = montar_contexto(instituicao=inst, usuario=u, aluno=a, responsavel=None)
        chaves = {c for _, itens in CATALOGO for c, _, _ in itens}
        self.assertEqual(set(ctx), chaves)
        self.assertEqual(ctx["nomeAluno"], a.nome)
        self.assertEqual(ctx["matriculaUnica"], a.codigo)
        self.assertEqual(ctx["nomeResponsavel"], r.nome)
        self.assertEqual(ctx["parentescoResponsavel"], "Mãe")

    def test_financeiro_oculto_sem_permissao(self):
        sincronizar_perfis()
        inst = criar_instituicao()
        adm = criar_usuario(inst, superuser=True)
        a = criar_aluno(inst, adm)
        sem = criar_usuario(inst)
        self.assertEqual(montar_contexto(instituicao=inst, usuario=sem, aluno=a)["saldoDevedor"], "—")
        self.assertEqual(montar_contexto(instituicao=inst, usuario=adm, aluno=a)["saldoDevedor"], "R$ 0,00")


class EmissaoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.inst = criar_instituicao()
        cls.adm = criar_usuario(cls.inst, "Administrador")
        cls.aluno = criar_aluno(cls.inst, cls.adm, nome="Maria <Teste>")

    def test_emitir_para_aluno_grava_instantaneo_e_auditoria(self):
        m = modelo(self.inst)
        doc = emitir_documento(modelo=m, usuario=self.adm, aluno=self.aluno)
        self.assertIn("Maria &lt;Teste&gt;", doc.html)
        self.assertIn(self.aluno.codigo, doc.html)
        m.conteudo = "<p>Outro texto</p>"
        m.save()
        doc.refresh_from_db()
        self.assertIn("Declaramos", doc.html)  # documento emitido não muda com o modelo
        self.assertTrue(RegistroAuditoria.objects.filter(acao="DOCUMENTO_EMITIDO", entidade_id=str(doc.pk)).exists())

    def test_regras_de_destinatario(self):
        self.assertRaises(ValidationError, emitir_documento, modelo=modelo(self.inst, nome="A"), usuario=self.adm)
        mr = modelo(self.inst, nome="R", destinatario="RESPONSAVEL")
        self.assertRaises(ValidationError, emitir_documento, modelo=mr, usuario=self.adm, aluno=self.aluno)
        r = vincular(self.aluno, self.inst)
        outro = criar_aluno(self.inst, self.adm)
        self.assertRaises(ValidationError, emitir_documento, modelo=mr, usuario=self.adm, aluno=outro, responsavel=r)
        doc = emitir_documento(modelo=mr, usuario=self.adm, responsavel=r, aluno=self.aluno)
        self.assertEqual(doc.responsavel, r)

    def test_modelo_inativo_nao_emite(self):
        m = modelo(self.inst, nome="Inativo", ativo=False)
        self.assertRaises(ValidationError, emitir_documento, modelo=m, usuario=self.adm, aluno=self.aluno)

    def test_cabecalho_so_quando_ativo_e_escapado(self):
        com = modelo(self.inst, nome="Com", usar_cabecalho=True)
        sem = modelo(self.inst, nome="Sem", usar_cabecalho=False)
        self.assertNotIn("doc-cabecalho", emitir_documento(modelo=com, usuario=self.adm, aluno=self.aluno).html)
        CabecalhoDocumento.objects.create(instituicao=self.inst, ativo=True, titulo="Colégio <i>X</i>",
                                          linhas="Rua A\nTel 1", rodape="rodapé")
        h = emitir_documento(modelo=com, usuario=self.adm, aluno=self.aluno).html
        self.assertIn("Colégio &lt;i&gt;X&lt;/i&gt;", h)
        self.assertIn("Tel 1", h)
        self.assertIn("doc-rodape", h)
        self.assertNotIn("doc-cabecalho", emitir_documento(modelo=sem, usuario=self.adm, aluno=self.aluno).html)


class TelasDocumentosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.inst = criar_instituicao()
        cls.adm = criar_usuario(cls.inst, "Administrador")
        cls.sec = criar_usuario(cls.inst, "Secretaria")
        cls.cons = criar_usuario(cls.inst, "Consulta")
        cls.aluno = criar_aluno(cls.inst, cls.adm)

    def entrar(self, u):
        self.client.force_login(u)

    def dados(self, **kw):
        d = {"nome": "Declaração A", "tipo": "DECL_MATRICULA", "destinatario": "ALUNO", "usar_cabecalho": "on",
             "ativo": "on", "conteudo": "<p>Olá [nomeAluno]</p>"}
        d.update(kw)
        return d

    def test_menu_e_acesso_por_perfil(self):
        self.entrar(self.adm)
        self.assertContains(self.client.get(reverse("core:dashboard")), reverse("documents:modelo_lista"))
        self.assertEqual(self.client.get(reverse("documents:modelo_lista")).status_code, 200)
        self.entrar(self.cons)
        self.assertEqual(self.client.get(reverse("documents:modelo_lista")).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:emitir_aluno", args=[self.aluno.pk])).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("documents:modelo_lista")).status_code, 302)

    def test_secretaria_emite_mas_nao_configura(self):
        self.entrar(self.sec)
        self.assertEqual(self.client.get(reverse("documents:modelo_novo")).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:cabecalho")).status_code, 403)
        self.assertEqual(self.client.post(reverse("documents:modelo_novo"), self.dados()).status_code, 403)
        self.assertEqual(self.client.get(reverse("documents:emitir_aluno", args=[self.aluno.pk])).status_code, 200)

    def test_criar_editar_e_validar_modelo(self):
        self.entrar(self.adm)
        r = self.client.post(reverse("documents:modelo_novo"), self.dados(conteudo="<p>[nomeInventado]</p>"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Variáveis inexistentes")
        r = self.client.post(reverse("documents:modelo_novo"), self.dados(conteudo="<p><br></p>"))
        self.assertContains(r, "Escreva o conteúdo")
        r = self.client.post(reverse("documents:modelo_novo"),
                             self.dados(conteudo='<p>[nomeAluno]<script>alert(1)</script></p>'))
        self.assertEqual(r.status_code, 302)
        m = ModeloDocumento.objects.get(nome="Declaração A")
        self.assertNotIn("script", m.conteudo)
        self.assertEqual(m.instituicao, self.inst)
        r = self.client.post(reverse("documents:modelo_novo"), self.dados())  # nome duplicado
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ModeloDocumento.objects.count(), 1)
        r = self.client.post(reverse("documents:modelo_editar", args=[m.pk]), self.dados(destinatario="RESPONSAVEL"))
        self.assertEqual(r.status_code, 302)
        m.refresh_from_db()
        self.assertEqual(m.destinatario, "RESPONSAVEL")
        self.assertTrue(RegistroAuditoria.objects.filter(acao="MODELO_DOCUMENTO_ALTERADO").exists())

    def test_tipo_invalido_rejeitado(self):
        self.entrar(self.adm)
        r = self.client.post(reverse("documents:modelo_novo"), self.dados(tipo="INVENTADO"))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(ModeloDocumento.objects.exists())

    def test_fluxo_emitir_na_ficha_do_aluno(self):
        m = modelo(self.inst)
        modelo(self.inst, nome="Inativo", ativo=False)
        modelo(self.inst, nome="Para responsável", destinatario="RESPONSAVEL")
        self.entrar(self.sec)
        ficha = self.client.get(reverse("students:aluno_ficha", args=[self.aluno.pk]))
        self.assertContains(ficha, reverse("documents:emitir_aluno", args=[self.aluno.pk]))
        pagina = self.client.get(reverse("documents:emitir_aluno", args=[self.aluno.pk]))
        self.assertContains(pagina, "Declaração de matrícula")
        self.assertNotContains(pagina, "Inativo")
        self.assertNotContains(pagina, "Para responsável")
        r = self.client.post(reverse("documents:emitir_aluno", args=[self.aluno.pk]), {"modelo": m.pk})
        doc = DocumentoEmitido.objects.get()
        self.assertRedirects(r, reverse("documents:documento", args=[doc.pk]))
        self.assertContains(self.client.get(r.url), self.aluno.nome)

    def test_modelo_de_responsavel_nao_emite_na_tela_do_aluno(self):
        mr = modelo(self.inst, nome="R", destinatario="RESPONSAVEL")
        self.entrar(self.sec)
        r = self.client.post(reverse("documents:emitir_aluno", args=[self.aluno.pk]), {"modelo": mr.pk})
        self.assertEqual(r.status_code, 404)

    def test_fluxo_responsavel_e_aluno_de_outro_responsavel(self):
        resp = vincular(self.aluno, self.inst)
        outro = criar_aluno(self.inst, self.adm)
        mr = modelo(self.inst, nome="R", destinatario="RESPONSAVEL", conteudo="<p>[nomeResponsavel] de [nomeAluno]</p>")
        self.entrar(self.sec)
        url = reverse("documents:emitir_responsavel", args=[resp.pk])
        self.assertContains(self.client.get(reverse("students:responsavel_detalhe", args=[resp.pk])), url)
        r = self.client.post(url, {"modelo": mr.pk, "aluno": outro.pk})  # não vinculado
        self.assertRedirects(r, url, fetch_redirect_response=False)
        self.assertFalse(DocumentoEmitido.objects.exists())
        r = self.client.post(url, {"modelo": mr.pk, "aluno": self.aluno.pk})
        doc = DocumentoEmitido.objects.get()
        self.assertIn(resp.nome, doc.html)
        self.assertIn(self.aluno.nome, doc.html)

    def test_isolamento_entre_instituicoes(self):
        outra = criar_instituicao("Outra")
        adm2 = criar_usuario(outra, "Administrador")
        m_outra = modelo(outra, nome="Da outra")
        doc = emitir_documento(modelo=modelo(self.inst, nome="Minha"), usuario=self.adm, aluno=self.aluno)
        self.entrar(adm2)
        self.assertEqual(self.client.get(reverse("documents:documento", args=[doc.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("documents:emitir_aluno", args=[self.aluno.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("documents:modelo_editar", args=[modelo(self.inst, nome="X").pk]))
                         .status_code, 404)
        self.assertNotContains(self.client.get(reverse("documents:modelo_lista")), "Minha")
        self.entrar(self.adm)
        r = self.client.post(reverse("documents:emitir_aluno", args=[self.aluno.pk]), {"modelo": m_outra.pk})
        self.assertEqual(r.status_code, 404)

    def test_previa_usa_somente_exemplos(self):
        self.entrar(self.adm)
        r = self.client.post(reverse("documents:previa"),
                             {"conteudo": "<p>[nomeAluno] [xpto]<script>1</script></p>", "usar_cabecalho": "0"})
        j = r.json()
        self.assertIn("Maria Souza Lima", j["html"])
        self.assertNotIn(self.aluno.nome, j["html"])
        self.assertNotIn("script", j["html"])
        self.assertEqual(j["desconhecidas"], ["xpto"])

    def test_csrf_na_emissao(self):
        from django.test import Client
        m = modelo(self.inst)
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.adm)
        r = c.post(reverse("documents:emitir_aluno", args=[self.aluno.pk]), {"modelo": m.pk})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(DocumentoEmitido.objects.exists())

    def test_cabecalho_com_logotipo(self):
        import tempfile
        buf = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(buf, "PNG")
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            self.entrar(self.adm)
            r = self.client.post(reverse("documents:cabecalho"), {
                "ativo": "on", "exibir_logotipo": "on", "titulo": "Minha Escola", "linhas": "Rua A",
                "rodape": "", "logotipo": SimpleUploadedFile("logo.png", buf.getvalue(), "image/png")})
            self.assertEqual(r.status_code, 302)
            cab = CabecalhoDocumento.objects.get(instituicao=self.inst)
            self.assertTrue(cab.logotipo.name.startswith("logotipos/"))
            m = modelo(self.inst)
            doc = emitir_documento(modelo=m, usuario=self.adm, aluno=self.aluno)
            self.assertIn("doc-logo", doc.html)
            r = self.client.post(reverse("documents:cabecalho"), {
                "ativo": "on", "logotipo": SimpleUploadedFile("x.png", b"nao e imagem", "image/png")})
            self.assertEqual(r.status_code, 200)
            self.assertFalse(CabecalhoDocumento.objects.get(instituicao=self.inst).titulo == "")
