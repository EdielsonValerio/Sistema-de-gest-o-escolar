"""Validação e reprocessamento de imagens enviadas por usuários.

* Limite de tamanho (bytes), de dimensões e de pixels (proteção contra "decompression bomb").
* Confere o conteúdo REAL com a Pillow (não confia na extensão nem no Content-Type informado).
* Só aceita JPEG, PNG e WebP.
* Reprocessa: aplica a orientação, remove EXIF/GPS e demais metadados, limita o lado maior e regrava o arquivo.
  O nome final é aleatório (o `upload_to` dos modelos também gera nomes aleatórios; nada do nome original é usado).
"""
import io
import secrets

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from PIL import Image, ImageOps, UnidentifiedImageError

FORMATOS = {"JPEG": ("jpg", "JPEG"), "PNG": ("png", "PNG"), "WEBP": ("webp", "WEBP")}
MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # a Pillow recusa (erro) acima do dobro e avisa acima deste valor


def processar_imagem(arquivo, *, max_bytes, max_lado_entrada=8000, lado_saida=1600, formato_saida=None, rotulo="A imagem"):
    """Devolve um ContentFile seguro, ou o próprio valor se não for um upload novo (ex.: arquivo já salvo)."""
    if not isinstance(arquivo, UploadedFile):
        return arquivo
    if arquivo.size > max_bytes:
        raise ValidationError(f"{rotulo} deve ter no máximo {max_bytes // (1024 * 1024)} MB.")
    try:
        arquivo.seek(0)
        img = Image.open(arquivo)
        formato = img.format
        img.verify()  # detecta arquivos truncados/malformados
        arquivo.seek(0)
        img = Image.open(arquivo)
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError(f"{rotulo} não é uma imagem válida. Envie PNG, JPG ou WebP.") from None
    if formato not in FORMATOS:
        raise ValidationError("Use uma imagem PNG, JPG ou WebP.")
    if max(img.size) > max_lado_entrada or img.size[0] * img.size[1] > MAX_PIXELS:
        raise ValidationError(f"{rotulo} é grande demais (máximo {max_lado_entrada} pixels de lado).")
    img = ImageOps.exif_transpose(img)  # respeita a orientação ANTES de descartar os metadados
    if max(img.size) > lado_saida:
        img.thumbnail((lado_saida, lado_saida), Image.LANCZOS)
    saida = formato_saida or ("PNG" if formato == "PNG" else "JPEG")
    if saida == "JPEG" and img.mode not in ("RGB", "L"):
        fundo = Image.new("RGB", img.size, "white")
        base = img.convert("RGBA")
        fundo.paste(base, mask=base.split()[-1])
        img = fundo
    elif saida == "PNG" and img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    kw = {"quality": 88, "optimize": True} if saida == "JPEG" else {"optimize": True}
    img.save(buf, saida, **kw)  # sem exif=: nenhum metadado é copiado
    ext = FORMATOS[saida][0]
    return ContentFile(buf.getvalue(), name=f"{secrets.token_hex(16)}.{ext}")
