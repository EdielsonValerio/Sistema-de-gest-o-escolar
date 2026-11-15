"""Botões Boletim e Histórico da ficha do aluno, e cadastro do histórico de outras escolas."""
from urllib.parse import quote

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils.safestring import mark_safe
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.audit.services import registrar
from apps.core.mixins import AcessoMixin
from apps.students.models import Aluno

from . import boletim as b
from . import emissao
from .models import HistoricoExterno, HistoricoExternoDisciplina


class _AlunoMixin(AcessoMixin):
    @property
    def aluno(self):
        return get_object_or_404(Aluno, pk=self.kwargs["pk"], instituicao=self.instituicao)

    def ir_para_documento(self, doc, voltar):
        url = f"{reverse('documents:documento', args=[doc.pk])}?voltar={quote(voltar)}"
        if self.request.POST.get("imprimir"):
            url += "&imprimir=1"  # abre direto a janela de impressão / salvar em PDF
        return redirect(url)


class BoletimAlunoView(_AlunoMixin, TemplateView):
    permission_required = ("diario.emitir_boletim", "students.view_aluno")
    template_name = "diario/boletim_aluno.html"

    def _matricula(self):
        mats = b.matriculas_do_aluno(self.aluno)
        escolhida = self.request.GET.get("matricula") or self.request.POST.get("matricula")
        m = next((x for x in mats if str(x.pk) == escolhida), None) or (mats[0] if mats else None)
        return mats, m

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        mats, m = self._matricula()
        d = b.dados_boletim(m) if m else None
        ctx.update(aluno=self.aluno, matriculas=mats, matricula=m, d=d,
                   tabela=mark_safe(b.tabela_boletim_html(d)) if d and d["linhas"] else "",  # nosec B308 B703: HTML montado com escape()
                   pode_gerar=bool(d and d["linhas"] and d["liberadas"]))
        return ctx

    def post(self, request, *args, **kwargs):
        _, m = self._matricula()
        if m is None:
            messages.error(request, "O aluno não tem matrícula em turma oficial.")
            return redirect(request.path)
        try:
            doc = emissao.emitir_boletim(m, request.user)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(f"{request.path}?matricula={m.pk}")
        return self.ir_para_documento(doc, f"{request.path}?matricula={m.pk}")


class HistoricoAlunoView(_AlunoMixin, TemplateView):
    permission_required = ("diario.emitir_historico", "students.view_aluno")
    template_name = "diario/historico_aluno.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        d = b.dados_historico(self.aluno)
        ctx.update(aluno=self.aluno, d=d, tabela=mark_safe(b.tabela_historico_html(d)),  # nosec B308 B703: HTML montado com escape()
                   pode_gerar=bool(d["blocos"]),
                   externos=HistoricoExterno.objects.filter(aluno=self.aluno))
        return ctx

    def post(self, request, *args, **kwargs):
        try:
            doc = emissao.emitir_historico(self.aluno, request.user)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect(request.path)
        return self.ir_para_documento(doc, request.path)


# --------------------------------------------------------------------------- histórico de outras escolas
class HistoricoExternoForm(forms.ModelForm):
    class Meta:
        model = HistoricoExterno
        fields = ["ano", "serie", "escola", "cidade_uf", "resultado", "observacoes"]


DisciplinasFormSet = inlineformset_factory(
    HistoricoExterno, HistoricoExternoDisciplina, fields=["nome", "media", "carga_horaria", "frequencia"],
    extra=6, can_delete=True, max_num=30)


class HistoricoExternoView(_AlunoMixin, TemplateView):
    """Cadastro/edição de um ano cursado em outra escola, com suas disciplinas."""

    permission_required = ("diario.gerenciar_historico_externo", "students.view_aluno")
    template_name = "diario/historico_externo_form.html"

    def _obj(self):
        if self.kwargs.get("ext_pk"):
            return get_object_or_404(HistoricoExterno, pk=self.kwargs["ext_pk"], aluno=self.aluno,
                                     instituicao=self.instituicao)
        return None

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        obj = self._obj()
        ctx.update(aluno=self.aluno, obj=obj,
                   form=kw.get("form") or HistoricoExternoForm(instance=obj),
                   formset=kw.get("formset") or DisciplinasFormSet(instance=obj or HistoricoExterno()))
        return ctx

    def post(self, request, *args, **kwargs):
        obj = self._obj()
        form = HistoricoExternoForm(request.POST, instance=obj)
        formset = DisciplinasFormSet(request.POST, instance=obj or HistoricoExterno())
        if not (form.is_valid() and formset.is_valid()):
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        novo = obj is None
        h = form.save(commit=False)
        h.instituicao, h.aluno = self.instituicao, self.aluno
        if novo:
            h.criado_por = request.user
        h.save()
        formset.instance = h
        for f in formset.save(commit=False):
            f.instituicao = self.instituicao
            f.save()
        for f in formset.deleted_objects:
            f.delete()
        registrar(instituicao=self.instituicao, usuario=request.user, aluno_id=self.aluno.pk,
                  acao="HISTORICO_EXTERNO_CRIADO" if novo else "HISTORICO_EXTERNO_ALTERADO",
                  entidade="HistoricoExterno", entidade_id=h.pk, descricao=f"Histórico de outra escola: {h}")
        messages.success(request, "Histórico de outra escola salvo.")
        return redirect("diario:historico_aluno", pk=self.aluno.pk)


class HistoricoExternoExcluirView(_AlunoMixin, View):
    permission_required = ("diario.gerenciar_historico_externo", "students.view_aluno")

    def post(self, request, pk, ext_pk):
        h = get_object_or_404(HistoricoExterno, pk=ext_pk, aluno=self.aluno, instituicao=self.instituicao)
        registrar(instituicao=self.instituicao, usuario=request.user, aluno_id=self.aluno.pk,
                  acao="HISTORICO_EXTERNO_EXCLUIDO", entidade="HistoricoExterno", entidade_id=h.pk,
                  descricao=f"Histórico de outra escola removido: {h}")
        h.delete()
        messages.success(request, "Registro removido.")
        return redirect("diario:historico_aluno", pk=self.aluno.pk)
