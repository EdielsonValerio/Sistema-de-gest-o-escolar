"""Parametrização da avaliação por escola — SOMENTE administrador do sistema (superusuário em modo suporte).

A escola não altera estas regras: o administrador monta o modelo (fases, componentes, fórmulas, recuperações, aprovação)
e o publica para as séries desejadas. O cálculo é feito no servidor por motor.py, com fórmulas avaliadas por formulas.py.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import TemplateView

from apps.core.mixins import AcessoMixin

from . import formulas, motor, servicos
from .forms import (ComponenteForm, DuplicarModeloForm, FaseForm, ModeloAvaliacaoForm, PresetModeloForm)
from .models import ComponenteNota, ModeloAvaliacao, PeriodoLetivo


class SistemaMixin(AcessoMixin):
    """Só superusuário ativo, com uma escola aberta (modo suporte). Escola e professor nunca passam daqui (403)."""

    permission_required = ()

    def has_permission(self):
        u = self.request.user
        if not (u.is_authenticated and u.is_superuser and u.is_active):
            return False
        return super().has_permission()  # exige escola aberta

    @cached_property
    def modelo(self):
        return get_object_or_404(ModeloAvaliacao.objects.select_related("ano_letivo"), pk=self.kwargs["pk"],
                                 instituicao=self.instituicao)

    def auditar(self, acao, obj, descricao, dados=None):
        servicos._audit(self.instituicao, self.request.user, acao, obj.__class__.__name__, obj.pk, descricao, dados)


class ModeloListView(SistemaMixin, TemplateView):
    template_name = "diario/modelo_lista.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        modelos = list(ModeloAvaliacao.objects.filter(instituicao=self.instituicao).select_related("ano_letivo")
                       .prefetch_related("series", "fases"))
        ctx.update(modelos=modelos, preset_form=kw.get("preset_form") or PresetModeloForm(instituicao=self.instituicao))
        return ctx

    def post(self, request, *args, **kwargs):  # criar a partir de um modelo pronto
        form = PresetModeloForm(request.POST, instituicao=self.instituicao)
        if form.is_valid():
            d = form.cleaned_data
            try:
                m = servicos.criar_de_preset(self.instituicao, d["ano_letivo"], d["nome"], d["modelo_pronto"], request.user)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                messages.success(request, "Modelo criado. Ajuste as séries, fórmulas e componentes e publique.")
                return redirect("diario:modelo", pk=m.pk)
        return self.render_to_response(self.get_context_data(preset_form=form))


class _FormBase(SistemaMixin, TemplateView):
    template_name = "generic/form.html"
    titulo = subtitulo = ""

    def get_form(self, data=None):
        raise NotImplementedError

    def voltar(self):
        raise NotImplementedError

    def salvar(self, form):
        raise NotImplementedError

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        ctx.update(form=kw.get("form") or self.get_form(), titulo=self.titulo, subtitulo=self.subtitulo, cancelar_url=self.voltar())
        return ctx

    def post(self, request, *args, **kwargs):
        form = self.get_form(request.POST)
        if form.is_valid():
            try:
                destino = self.salvar(form)
            except ValidationError as exc:
                form.add_error(None, exc.messages[0])
            else:
                return redirect(destino or self.voltar())
        return self.render_to_response(self.get_context_data(form=form))


class ModeloFormView(_FormBase):
    editando = False
    subtitulo = "Parametrização feita pelo administrador do sistema."

    @property
    def titulo(self):
        return "Editar modelo de avaliação" if self.editando else "Novo modelo de avaliação"

    @cached_property
    def objeto(self):
        return self.modelo if self.editando else None

    def get_form(self, data=None):
        return ModeloAvaliacaoForm(data, instance=self.objeto, instituicao=self.instituicao)

    def voltar(self):
        return reverse("diario:modelo", args=[self.objeto.pk]) if self.objeto else reverse("diario:modelos")

    def salvar(self, form):
        novo = self.objeto is None
        m = form.save()
        form.save_m2m()
        self.auditar("MODELO_AVALIACAO_CRIADO" if novo else "MODELO_AVALIACAO_ALTERADO", m,
                     f"Modelo {'criado' if novo else 'alterado'}: {m}", {"campos": form.changed_data})
        messages.success(self.request, "Modelo salvo.")
        return reverse("diario:modelo", args=[m.pk])


class ModeloDetalheView(SistemaMixin, TemplateView):
    template_name = "diario/modelo_detalhe.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        m = self.modelo
        erros, avisos = servicos.validar_modelo(m)
        fases = list(m.fases.prefetch_related("componentes").order_by("ordem"))
        ctx.update(m=m, fases=fases, erros=erros, avisos=avisos, travada=servicos.estrutura_travada(m),
                   publicado=m.situacao == ModeloAvaliacao.Situacao.PUBLICADO, series=list(m.series.all()),
                   formula_final=m.formula_final or "MP (média ponderada pelos pesos das fases)",
                   formula_rec=m.formula_rec_final or motor.FORMULA_REC_FINAL_PADRAO,
                   vars_final=", ".join(motor.nomes_formula_final(len(fases))))
        return ctx


class _Acao(SistemaMixin, View):
    """POST de uma ação sobre o modelo."""

    def executar(self):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        try:
            msg = self.executar()
            messages.success(request, msg)
        except ValidationError as exc:
            for e in exc.messages:
                messages.error(request, e)
        return redirect("diario:modelo", pk=self.modelo.pk)


class GerarFasesView(_Acao):
    def executar(self):
        servicos.gerar_fases(self.modelo, self.request.user)
        return "Fases geradas. Ajuste as datas e os pesos se precisar."


class PublicarModeloView(_Acao):
    def executar(self):
        servicos.publicar_modelo(self.modelo, self.request.user)
        return "Modelo publicado: passa a valer para as turmas das séries atendidas."


class DespublicarModeloView(_Acao):
    def executar(self):
        servicos.despublicar_modelo(self.modelo, self.request.user)
        return "Modelo despublicado (voltou a rascunho)."


class DuplicarModeloView(_FormBase):
    subtitulo = "Copia fases, componentes, fórmulas e séries, ajustando as datas ao novo ano letivo."

    @property
    def titulo(self):
        return f"Duplicar modelo — {self.modelo.nome}"

    def get_form(self, data=None):
        return DuplicarModeloForm(data, instituicao=self.instituicao,
                                  initial={"nome": f"{self.modelo.nome} (cópia)"})

    def voltar(self):
        return reverse("diario:modelo", args=[self.modelo.pk])

    def salvar(self, form):
        novo = servicos.duplicar_modelo(self.modelo, form.cleaned_data["ano_letivo"], form.cleaned_data["nome"], self.request.user)
        messages.success(self.request, "Modelo duplicado em rascunho.")
        return reverse("diario:modelo", args=[novo.pk])


# ----------------------------------------------------------------------------- fases e componentes
class FaseFormView(_FormBase):
    editando = False
    subtitulo = "Cada fase corresponde a um período do ano (bimestre, trimestre, mês...)."

    @property
    def titulo(self):
        return "Editar fase" if self.editando else "Nova fase"

    @cached_property
    def fase(self):
        return get_object_or_404(PeriodoLetivo, pk=self.kwargs["fase_pk"], modelo=self.modelo, instituicao=self.instituicao) \
            if self.editando else None

    def get_form(self, data=None):
        initial = {} if self.editando else {"ordem": self.modelo.fases.count() + 1}
        return FaseForm(data, instance=self.fase, instituicao=self.instituicao, modelo=self.modelo, initial=initial)

    def voltar(self):
        return reverse("diario:modelo", args=[self.modelo.pk])

    def salvar(self, form):
        servicos._exigir_rascunho_ou_livre(self.modelo)
        f = form.save(commit=False)
        f.modelo, f.ano_letivo, f.instituicao = self.modelo, self.modelo.ano_letivo, self.instituicao
        f.save()
        self.auditar("MODELO_AVALIACAO_FASE_SALVA", f, f"Fase salva: {f.nome}", {"modelo": self.modelo.pk})
        messages.success(self.request, "Fase salva.")


class ComponenteFormView(_FormBase):
    editando = False
    subtitulo = "Parte da nota da fase. O professor escolhe o componente ao criar cada avaliação."

    @property
    def titulo(self):
        return "Editar componente da nota" if self.editando else "Novo componente da nota"

    @cached_property
    def fase_obj(self):
        return get_object_or_404(PeriodoLetivo, pk=self.kwargs["fase_pk"], modelo=self.modelo, instituicao=self.instituicao)

    @cached_property
    def componente(self):
        return get_object_or_404(ComponenteNota, pk=self.kwargs["comp_pk"], fase=self.fase_obj) if self.editando else None

    def get_form(self, data=None):
        initial = {} if self.editando else {"ordem": self.fase_obj.componentes.count() + 1}
        return ComponenteForm(data, instance=self.componente, instituicao=self.instituicao, fase=self.fase_obj, initial=initial)

    def voltar(self):
        return reverse("diario:modelo", args=[self.modelo.pk])

    def salvar(self, form):
        servicos._exigir_rascunho_ou_livre(self.modelo)
        c = form.save(commit=False)
        c.fase, c.instituicao = self.fase_obj, self.instituicao
        c.save()
        self.auditar("MODELO_AVALIACAO_COMPONENTE_SALVO", c, f"Componente salvo: {c.nome} ({c.sigla})", {"modelo": self.modelo.pk})
        messages.success(self.request, "Componente salvo.")


class _Excluir(SistemaMixin, View):
    def post(self, request, *args, **kwargs):
        try:
            servicos._exigir_rascunho_ou_livre(self.modelo)
            obj = self.alvo()
            nome = str(obj)
            obj.delete()
            self.auditar("MODELO_AVALIACAO_ITEM_REMOVIDO", self.modelo, f"Removido: {nome}")
            messages.success(request, "Removido.")
        except ProtectedError:
            messages.error(request, "Existem avaliações lançadas vinculadas a este item; não é possível remover.")
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        return redirect("diario:modelo", pk=self.modelo.pk)


class FaseExcluirView(_Excluir):
    def alvo(self):
        return get_object_or_404(PeriodoLetivo, pk=self.kwargs["fase_pk"], modelo=self.modelo, instituicao=self.instituicao)


class ComponenteExcluirView(_Excluir):
    def alvo(self):
        return get_object_or_404(ComponenteNota, pk=self.kwargs["comp_pk"], fase__modelo=self.modelo, instituicao=self.instituicao)


# ----------------------------------------------------------------------------- simulador
def _dec(txt):
    txt = (txt or "").strip().replace(",", ".")
    if not txt:
        return None
    try:
        v = Decimal(txt)
    except InvalidOperation:
        raise ValidationError(f"Valor inválido: {txt}")
    if v < 0:
        raise ValidationError("Use valores iguais ou maiores que zero.")
    return v


class SimuladorView(SistemaMixin, TemplateView):
    template_name = "diario/simulador.html"

    def get_context_data(self, **kw):
        ctx = super().get_context_data(**kw)
        m = self.modelo
        fases = list(m.fases.prefetch_related("componentes").order_by("ordem"))
        dados = kw.get("dados") or {}
        for f in fases:  # valores digitados voltam ao formulário (o template não indexa dicionário por chave dinâmica)
            f.v_nota, f.v_rp = dados.get(f"f_{f.pk}", ""), dados.get(f"rp_{f.pk}", "")
            for c in f.componentes.all():
                c.v = dados.get(f"c_{c.pk}", "")
        ctx.update(m=m, fases=fases, dados=dados, resultado=kw.get("resultado"), erro=kw.get("erro"))
        return ctx

    def post(self, request, *args, **kwargs):
        m = self.modelo
        fases = list(m.fases.prefetch_related("componentes").order_by("ordem"))
        P = request.POST
        try:
            brutas, rps = [], []
            for f in fases:
                comps = list(f.componentes.all())
                if comps:
                    brutas.append(motor.nota_da_fase(m, f, comps, {c.sigla: _dec(P.get(f"c_{c.pk}")) for c in comps}))
                else:
                    brutas.append(_dec(P.get(f"f_{f.pk}")))
                rps.append(_dec(P.get(f"rp_{f.pk}")) if m.usa_rec_parcial else None)
            rf = _dec(P.get("rf")) if m.usa_rec_final else None
            freq = _dec(P.get("freq"))
            r = motor.fechar(m, fases, brutas, rps, rf, freq)
            r["fases"] = [{"fase": f, "bruta": b, "final": r["medias"][i]} for i, (f, b) in enumerate(zip(fases, brutas))]
            return self.render_to_response(self.get_context_data(dados=P, resultado=r))
        except (ValidationError, formulas.FormulaErro) as exc:
            msg = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            return self.render_to_response(self.get_context_data(dados=P, erro=msg))
