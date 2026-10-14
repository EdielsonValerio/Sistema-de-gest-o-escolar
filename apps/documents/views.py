from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from apps.audit.services import registrar
from apps.core.mixins import (AcessoMixin, FormKwargsInstituicaoMixin, InstituicaoQuerysetMixin, ObjetoFormView,
                              PaginacaoMixin)
from apps.students.models import Aluno, Responsavel

from .forms import CabecalhoDocumentoForm, ModeloDocumentoForm
from .models import CabecalhoDocumento, Destinatario, DocumentoEmitido, ModeloDocumento
from .services import emitir_documento, previa_html
from .tipos import ROTULO_TIPO, TIPOS_DOCUMENTO
from .variaveis import CATALOGO, variaveis_desconhecidas


class ModeloListView(PaginacaoMixin, InstituicaoQuerysetMixin, ListView):
    model = ModeloDocumento
    permission_required = "documents.view_modelodocumento"
    template_name = "documents/modelo_lista.html"
    context_object_name = "modelos"

    def get_queryset(self):
        qs = super().get_queryset()
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(nome__icontains=g["q"].strip())
        if g.get("tipo") in ROTULO_TIPO:
            qs = qs.filter(tipo=g["tipo"])
        if g.get("destinatario") in Destinatario.values:
            qs = qs.filter(destinatario=g["destinatario"])
        if g.get("ativo") in ("1", "0"):
            qs = qs.filter(ativo=g["ativo"] == "1")
        return qs

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        g = self.request.GET
        ctx.update(
            titulo="Documentos", rotulos=ROTULO_TIPO, tipos=TIPOS_DOCUMENTO, destinatarios=Destinatario.choices,
            subtitulo="Modelos dos documentos emitidos pela secretaria e pelo financeiro: tipo, leiaute e destinatário.",
            filtros={k: g.get(k, "") for k in ("q", "tipo", "destinatario", "ativo")},
            tem_filtro=any(g.get(k) for k in ("q", "tipo", "destinatario", "ativo")),
            novo_url=reverse("documents:modelo_novo") if self.request.user.has_perm("documents.add_modelodocumento") else None,
            novo_rotulo="Novo modelo")
        return ctx


class _ModeloFormBase(FormKwargsInstituicaoMixin, ObjetoFormView):
    model = ModeloDocumento
    form_class = ModeloDocumentoForm
    template_name = "documents/modelo_form.html"
    sucesso_url_name = "documents:modelo_lista"
    subtitulo = "Escreva o leiaute no editor e insira variáveis do cadastro (ex.: [nomeAluno])."

    def get_extra_context(self):
        return {"catalogo": CATALOGO, "tipos_rotulos": ROTULO_TIPO, "previa_url": reverse("documents:previa")}

    def apos_salvar(self, obj, form, novo):
        registrar(instituicao=self.instituicao, usuario=self.request.user,
                  acao="MODELO_DOCUMENTO_CRIADO" if novo else "MODELO_DOCUMENTO_ALTERADO",
                  entidade="ModeloDocumento", entidade_id=obj.pk, descricao=f"Modelo de documento: {obj.nome}",
                  dados={"tipo": obj.tipo, "destinatario": obj.destinatario, "ativo": obj.ativo})


class ModeloCreateView(_ModeloFormBase):
    permission_required = "documents.add_modelodocumento"
    titulo_novo = "Novo modelo de documento"
    mensagem_sucesso = "Modelo criado."


class ModeloUpdateView(_ModeloFormBase):
    permission_required = "documents.change_modelodocumento"
    editando = True
    titulo_editar = "Editar modelo de documento"
    mensagem_sucesso = "Modelo salvo. Documentos já emitidos não são alterados."


class PreviaView(AcessoMixin, View):
    """Pré-visualização do editor com valores de exemplo (nenhum dado real é lido)."""

    permission_required = "documents.view_modelodocumento"

    def post(self, request):
        conteudo = request.POST.get("conteudo", "")
        html = previa_html(conteudo=conteudo, usar_cabecalho=request.POST.get("usar_cabecalho") == "1",
                           instituicao=self.instituicao)
        return JsonResponse({"html": html, "desconhecidas": variaveis_desconhecidas(conteudo)})


class CabecalhoView(AcessoMixin, TemplateView):
    permission_required = "documents.change_cabecalhodocumento"
    template_name = "generic/form.html"

    def _obj(self):
        obj, _ = CabecalhoDocumento.objects.get_or_create(instituicao=self.instituicao)
        return obj

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(titulo="Cabeçalho padrão dos documentos",
                   subtitulo="Logotipo, título e linhas que abrem os documentos que usam o cabeçalho padrão.",
                   cancelar_url=reverse("documents:modelo_lista"),
                   form=kw.get("form") or CabecalhoDocumentoForm(instance=self._obj(), instituicao=self.instituicao))
        return ctx

    def post(self, request, *args, **kwargs):
        form = CabecalhoDocumentoForm(request.POST, request.FILES, instance=self._obj(), instituicao=self.instituicao)
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        obj = form.save()
        registrar(instituicao=self.instituicao, usuario=request.user, acao="CABECALHO_DOCUMENTO_ALTERADO",
                  entidade="CabecalhoDocumento", entidade_id=obj.pk, descricao="Cabeçalho padrão alterado",
                  dados={"ativo": obj.ativo, "logotipo": bool(obj.logotipo)})
        messages.success(request, "Cabeçalho salvo.")
        return redirect("documents:cabecalho")


class _EmitirBase(AcessoMixin, TemplateView):
    template_name = "documents/emitir.html"
    destinatario = None

    def get_alvo(self):
        raise NotImplementedError

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        modelos = ModeloDocumento.objects.filter(instituicao=self.instituicao, ativo=True,
                                                 destinatario=self.destinatario).order_by("tipo", "nome")
        ctx.update(modelos=modelos, rotulos=ROTULO_TIPO, destinatario=self.destinatario, **self.contexto_alvo())
        return ctx

    def post(self, request, *args, **kwargs):
        modelo = get_object_or_404(ModeloDocumento, pk=request.POST.get("modelo"), instituicao=self.instituicao,
                                   destinatario=self.destinatario)
        try:
            doc = emitir_documento(modelo=modelo, usuario=request.user, **self.argumentos_emissao())
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(request.path)
        messages.success(request, f"Documento nº {doc.numero} gerado.")
        return redirect("documents:documento", pk=doc.pk)


class EmitirAlunoView(_EmitirBase):
    permission_required = ("documents.emitir_documento", "students.view_aluno")
    destinatario = Destinatario.ALUNO

    @property
    def aluno(self):
        return get_object_or_404(Aluno, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def contexto_alvo(self):
        a = self.aluno
        return {"alvo": a, "alvo_nome": a.nome, "voltar_url": reverse("students:aluno_ficha", args=[a.pk]),
                "emitidos": a.documentos_emitidos.filter(destinatario=Destinatario.ALUNO)[:15]}

    def argumentos_emissao(self):
        return {"aluno": self.aluno}


class EmitirResponsavelView(_EmitirBase):
    permission_required = ("documents.emitir_documento", "students.view_responsavel")
    destinatario = Destinatario.RESPONSAVEL

    @property
    def responsavel(self):
        return get_object_or_404(Responsavel, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def _alunos(self):
        vistos = {}
        for v in self.responsavel.vinculos_alunos.select_related("aluno").order_by("fim", "-inicio"):
            vistos.setdefault(v.aluno_id, v.aluno)
        return list(vistos.values())

    def contexto_alvo(self):
        r = self.responsavel
        return {"alvo": r, "alvo_nome": r.nome, "alunos_vinculados": self._alunos(),
                "voltar_url": reverse("students:responsavel_detalhe", args=[r.pk]),
                "emitidos": r.documentos_emitidos.all()[:15]}

    def argumentos_emissao(self):
        aluno = None
        aluno_id = self.request.POST.get("aluno")
        if aluno_id:
            aluno = next((a for a in self._alunos() if str(a.pk) == aluno_id), None)
            if aluno is None:
                raise ValidationError("O aluno informado não está vinculado a este responsável.")
        return {"responsavel": self.responsavel, "aluno": aluno}

    def post(self, request, *args, **kwargs):
        try:
            self.argumentos_emissao()
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(request.path)
        return super().post(request, *args, **kwargs)


class DocumentoView(InstituicaoQuerysetMixin, DetailView):
    model = DocumentoEmitido
    permission_required = "documents.view_documentoemitido"
    template_name = "documents/documento.html"
    context_object_name = "doc"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        d = self.object
        if d.destinatario == Destinatario.ALUNO:
            ctx["voltar_url"] = reverse("documents:emitir_aluno", args=[d.aluno_id])
        else:
            ctx["voltar_url"] = reverse("documents:emitir_responsavel", args=[d.responsavel_id])
        return ctx
