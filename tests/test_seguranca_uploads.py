"""Uploads: tamanho, conteúdo real, metadados, nome aleatório e foto privada por escola."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.accounts.permissions import sincronizar_perfis
from apps.core.uploads import processar_imagem

from .helpers import criar_aluno, criar_instituicao, criar_usuario
import tempfile

HASH = ["django.contrib.auth.hashers.MD5PasswordHasher"]


def img_bytes(formato="JPEG", tamanho=(40, 30), exif=False):
    buf = io.BytesIO()
    im = Image.new("RGB", tamanho, "red")
    kw = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "CameraSecreta"  # fabricante
        e[0x8825] = {1: "S", 2: (3.0, 0.0, 0.0)}  # GPS
        kw["exif"] = e
    im.save(buf, formato, **kw)
    return buf.getvalue()


class ProcessarImagemTests(TestCase):
    def up(self, dados, nome="x.png", tipo="image/png"):
        return SimpleUploadedFile(nome, dados, content_type=tipo)

    def test_remove_exif_e_gera_nome_aleatorio(self):
        arq = processar_imagem(self.up(img_bytes("JPEG", exif=True), "../../etc/passwd.jpg", "image/jpeg"), max_bytes=1 << 20,
                               formato_saida="JPEG")
        self.assertRegex(arq.name, r"^[0-9a-f]{32}\.jpg$")
        out = Image.open(io.BytesIO(arq.read()))
        self.assertEqual(len(out.getexif()), 0)
        self.assertNotIn(b"CameraSecreta", arq.read() or b"")

    def test_recusa_conteudo_falso_mesmo_com_extensao_de_imagem(self):
        from django.core.exceptions import ValidationError
        for conteudo in (b"<?php system($_GET[0]); ?>", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
                         b"MZ\x90\x00executavel", img_bytes("PNG")[:30]):
            with self.assertRaises(ValidationError):
                processar_imagem(self.up(conteudo, "foto.png"), max_bytes=1 << 20)

    def test_recusa_formato_nao_permitido_e_arquivo_grande(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            processar_imagem(self.up(img_bytes("GIF"), "a.gif", "image/gif"), max_bytes=1 << 20)
        with self.assertRaises(ValidationError):
            processar_imagem(self.up(img_bytes("PNG"), "a.png"), max_bytes=10)
        with self.assertRaises(ValidationError):  # dimensões excessivas
            processar_imagem(self.up(img_bytes("PNG", (3000, 10)), "a.png"), max_bytes=1 << 22, max_lado_entrada=2000)

    def test_reduz_imagem_grande(self):
        arq = processar_imagem(self.up(img_bytes("PNG", (3000, 2000))), max_bytes=1 << 24, lado_lado=None) if False else \
            processar_imagem(self.up(img_bytes("PNG", (3000, 2000))), max_bytes=1 << 24, lado_saida=500)
        self.assertEqual(max(Image.open(io.BytesIO(arq.read())).size), 500)


@override_settings(PASSWORD_HASHERS=HASH, MFA_OBRIGATORIA=False, MEDIA_ROOT=tempfile.mkdtemp())
class FotoPrivadaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        sincronizar_perfis()
        cls.a, cls.b = criar_instituicao("A"), criar_instituicao("B")
        cls.sec_a = criar_usuario(cls.a, "Secretaria")
        cls.sec_b = criar_usuario(cls.b, "Secretaria")
        cls.prof_a = criar_usuario(cls.a, "Professor")
        cls.aluno = criar_aluno(cls.a, cls.sec_a)

    def cli(self, u, escola):
        c = Client()
        c.force_login(u)
        s = c.session
        s["escola_id"] = escola.pk
        s.save()
        return c

    def test_upload_pelo_formulario_processa_e_entrega_so_autenticado(self):
        c = self.cli(self.sec_a, self.a)
        r = c.post(reverse("students:aluno_editar", args=[self.aluno.pk]), self._dados(foto=SimpleUploadedFile(
            "f.jpg", img_bytes("JPEG", (50, 50), exif=True), content_type="image/jpeg")))
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        self.aluno.refresh_from_db()
        self.assertRegex(self.aluno.foto.name, r"alunos/\d+/[0-9a-f]{32}\.jpg$")
        url = reverse("students:aluno_foto", args=[self.aluno.pk])
        ok = c.get(url)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok["Cache-Control"], "private, no-store")
        self.assertEqual(Client().get(url).status_code, 302)  # sem login
        self.assertEqual(self.cli(self.sec_b, self.b).get(url).status_code, 404)  # outra escola
        self.assertEqual(self.cli(self.prof_a, self.a).get(url).status_code, 403)  # sem permissão
        # a ficha não expõe o caminho do arquivo
        self.assertNotIn(self.aluno.foto.name, c.get(reverse("students:aluno_ficha", args=[self.aluno.pk])).content.decode())
        # e o formulário de edição também não
        self.assertNotIn(self.aluno.foto.name, c.get(reverse("students:aluno_editar", args=[self.aluno.pk])).content.decode())

    def test_arquivo_falso_como_foto_e_recusado(self):
        c = self.cli(self.sec_a, self.a)
        r = c.post(reverse("students:aluno_editar", args=[self.aluno.pk]), self._dados(foto=SimpleUploadedFile(
            "f.jpg", b"<html><script>1</script></html>", content_type="image/jpeg")))
        self.assertEqual(r.status_code, 200)
        self.aluno.refresh_from_db()
        self.assertFalse(self.aluno.foto)

    def _dados(self, **extra):
        a = self.aluno
        return {"nome": a.nome, "data_nascimento": "2015-05-10", "sexo": "N", "nacionalidade": "Brasileira",
                "responsavel_legal": "PAIS", "ativo": "on", "resp_tipo": "", **extra}
