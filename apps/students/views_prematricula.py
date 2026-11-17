"""Pré-matrícula online: link público por escola (configuração) e formulário aberto ao responsável."""
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin

from .forms import PreMatriculaForm
from .models import LinkPreMatricula
from .services import cadastrar_pre_matricula

LIMITE_ENVIOS = 5          # pré-matrículas por endereço (IP) ...
JANELA_SEGUNDOS = 60 * 60  # ... por hora
SESSAO_CONCLUIDA = "pre_matricula_concluida"


def url_publica(request, link):
    caminho = reverse("students:pre_matricula_publica", args=[link.token])
    return f"{settings.SITE_URL}{caminho}" if settings.SITE_URL else request.build_absolute_uri(caminho)


class PreMatriculaConfigView(AcessoMixin, TemplateView):
    """Configurações > Pré-matrícula online: copiar, ativar/desativar e renovar o link."""

    permission_required = "students.change_linkprematricula"
    template_name = "students/pre_matricula_config.html"

    def _link(self):
        link, _ = LinkPreMatricula.objects.get_or_create(instituicao=self.instituicao)
        return link

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        link = self._link()
        from .models import Aluno
        ctx.update(link=link, url=url_publica(self.request, link), titulo_padrao=LinkPreMatricula.TITULO_PADRAO,
                   mensagem_padrao=LinkPreMatricula.MENSAGEM_PADRAO,
                   total_pre=Aluno.objects.filter(instituicao=self.instituicao, pre_matricula_online=True).count())
        return ctx

    def post(self, request, *args, **kwargs):
        link = self._link()
        acao = request.POST.get("acao")
        if acao in ("ativar", "desativar"):
            link.ativo = acao == "ativar"
            link.save(update_fields=["ativo", "atualizado_em"])
            texto = "Link ativado." if link.ativo else "Link desativado: o formulário público deixou de funcionar."
            audit = "PRE_MATRICULA_LINK_ATIVADO" if link.ativo else "PRE_MATRICULA_LINK_DESATIVADO"
        elif acao == "mensagem":
            titulo = " ".join(request.POST.get("titulo_conclusao", "").split())
            mensagem = request.POST.get("mensagem_conclusao", "").replace("\r\n", "\n").strip()
            if len(titulo) > 80 or len(mensagem) > 1000:
                messages.error(request, "Título (até 80 caracteres) ou mensagem (até 1000) acima do limite.")
                return redirect("students:pre_matricula_config")
            link.titulo_conclusao, link.mensagem_conclusao = titulo, mensagem
            link.save(update_fields=["titulo_conclusao", "mensagem_conclusao", "atualizado_em"])
            texto = "Mensagem final da pré-matrícula salva." if (titulo or mensagem) else \
                "Mensagem final restaurada para o texto padrão."
            audit = "PRE_MATRICULA_MENSAGEM_ALTERADA"
        elif acao == "renovar":
            link.token = LinkPreMatricula.novo_token()
            link.save(update_fields=["token", "atualizado_em"])
            texto = "Novo link gerado. O endereço anterior deixou de funcionar; divulgue o novo."
            audit = "PRE_MATRICULA_LINK_RENOVADO"
        else:
            return redirect("students:pre_matricula_config")
        registrar(instituicao=self.instituicao, usuario=request.user, acao=audit, entidade="LinkPreMatricula",
                  entidade_id=link.pk, descricao=texto)
        messages.success(request, texto)
        return redirect("students:pre_matricula_config")


def _link_publico(token):
    link = get_object_or_404(LinkPreMatricula.objects.select_related("instituicao"), token=token, ativo=True,
                             instituicao__ativa=True)
    if not link.instituicao.tem_modulo("prematricula"):  # módulo não contratado: o link público deixa de existir
        raise Http404
    return link


def _ip(request):
    """IP do visitante. Atrás de proxy reverso (nginx etc.), ligue DJANGO_CONFIAR_PROXY: usa o último endereço
    de X-Forwarded-For (o que o seu proxy acrescentou), que o visitante não consegue forjar."""
    if getattr(settings, "PRE_MATRICULA_CONFIAR_PROXY", False):
        encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if encaminhado:
            return encaminhado.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


class PreMatriculaPublicaView(View):
    """Formulário aberto (sem login). Cria o aluno como "Pré-matrícula online" na escola dona do link."""

    template = "students/pre_matricula_publica.html"

    def _render(self, request, link, form, status=200):
        cpf_dup = PreMatriculaForm.MSG_CPF_DUPLICADO in form.errors.get("cpf", [])
        resp = render(request, self.template, {"escola": link.instituicao, "form": form, "cpf_duplicado": cpf_dup},
                      status=status)
        resp["Cache-Control"] = "no-store"
        resp["X-Robots-Tag"] = "noindex, nofollow"
        return resp

    def get(self, request, token):
        link = _link_publico(token)
        return self._render(request, link, PreMatriculaForm(instituicao=link.instituicao))

    def post(self, request, token):
        link = _link_publico(token)
        escola = link.instituicao
        form = PreMatriculaForm(request.POST, instituicao=escola)
        if request.POST.get("contato_alternativo"):  # robô preencheu a isca: finge sucesso e não grava nada
            return redirect("students:pre_matricula_concluida", token=token)
        chave = f"premat:{_ip(request)}"
        if cache.get(chave, 0) >= LIMITE_ENVIOS:
            form.add_error(None, "Muitos envios deste endereço em pouco tempo. Tente novamente mais tarde "
                                 "ou procure o colégio.")
            return self._render(request, link, form, status=429)
        if not form.is_valid():
            return self._render(request, link, form)
        try:
            aluno = cadastrar_pre_matricula(form=form, instituicao=escola)
        except IntegrityError:  # CPF cadastrado por outra requisição ao mesmo tempo
            form.add_error("cpf", PreMatriculaForm.MSG_CPF_DUPLICADO)
            return self._render(request, link, form)
        try:
            cache.add(chave, 0, JANELA_SEGUNDOS)
            cache.incr(chave)
        except ValueError:
            cache.set(chave, 1, JANELA_SEGUNDOS)
        request.session[SESSAO_CONCLUIDA] = {"codigo": aluno.codigo, "nome": aluno.nome}
        return redirect("students:pre_matricula_concluida", token=token)


class PreMatriculaConcluidaView(View):
    def get(self, request, token):
        link = _link_publico(token)
        dados = request.session.pop(SESSAO_CONCLUIDA, None)
        titulo, mensagem = link.textos_conclusao(dados)
        resp = render(request, "students/pre_matricula_concluida.html",
                      {"escola": link.instituicao, "dados": dados, "titulo": titulo, "mensagem": mensagem})
        resp["Cache-Control"] = "no-store"
        resp["X-Robots-Tag"] = "noindex, nofollow"
        return resp

