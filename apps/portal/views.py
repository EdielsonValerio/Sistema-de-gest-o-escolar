"""Telas do portal do responsável (somente leitura, exceto gerar documentos liberados pela escola)."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views import View

from apps.audit.services import registrar
from apps.billing.models import Cobranca
from apps.diario import boletim as b
from apps.documents.models import Destinatario, DocumentoEmitido, ModeloDocumento
from apps.documents.services import emitir_documento
from apps.documents.tipos import TIPOS_SOMENTE_FICHA
from apps.finance.models import Parcela

from . import services as sv
from .models import AcessoResponsavel, ConfiguracaoPortal, DocumentoPortal

LIMITE_DOCUMENTOS_HORA = 20


def _sem_cache(resp):
    resp["Cache-Control"] = "no-store"
    resp["X-Robots-Tag"] = "noindex, nofollow"
    return resp


class PortalView(View):
    """Base: exige acesso válido (revalidado a cada requisição) e define `self.acesso`."""

    def dispatch(self, request, *args, **kwargs):
        self.acesso = sv.acesso_da_sessao(request)
        if self.acesso is None:
            slug = request.session.get(sv.S_SLUG)
            if slug and ConfiguracaoPortal.objects.filter(slug=slug).exists():
                return redirect("portal:login", slug=slug)
            raise Http404("Use o endereço do portal enviado pela sua escola.")
        self.escola = self.acesso.instituicao
        return _sem_cache(super().dispatch(request, *args, **kwargs))

    def pagina(self, request, template, **ctx):
        return render(request, template, {"escola": self.escola, "acesso": self.acesso, **ctx})

    def aluno(self, pk, **flags):
        """Aluno do responsável, ou 404 (nunca 403: não revela se o ID existe)."""
        return get_object_or_404(sv.alunos_do_acesso(self.acesso, **flags), pk=pk)


# --------------------------------------------------------------------------- entrada
class LoginForm(forms.Form):
    cpf = forms.CharField(label="CPF", max_length=14)
    senha = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput)


def _config_publica(slug):
    cfg = ConfiguracaoPortal.objects.select_related("instituicao").filter(slug=slug).first()
    if cfg is None or not cfg.ativo or not cfg.instituicao.ativa:
        raise Http404
    return cfg


class LoginView(View):
    def get(self, request, slug):
        cfg = _config_publica(slug)
        if sv.acesso_da_sessao(request):
            return redirect("portal:inicio")
        return _sem_cache(render(request, "portal/login.html", {"escola": cfg.instituicao, "form": LoginForm()}))

    def post(self, request, slug):
        cfg = _config_publica(slug)
        form = LoginForm(request.POST)
        erro = ""
        if form.is_valid():
            acesso, bloqueado = sv.autenticar(request, cfg, form.cleaned_data["cpf"], form.cleaned_data["senha"])
            if acesso:
                sv.entrar(request, acesso, slug)
                return redirect("portal:inicio")
            erro = ("Muitas tentativas. Aguarde alguns minutos e tente novamente." if bloqueado else sv.ERRO_LOGIN)
        else:
            erro = sv.ERRO_LOGIN
        r = render(request, "portal/login.html", {"escola": cfg.instituicao, "form": LoginForm(), "erro": erro},
                   status=200)
        return _sem_cache(r)


class SairView(View):
    def post(self, request):
        slug = request.session.get(sv.S_SLUG)
        sv.sair(request)
        return redirect("portal:login", slug=slug) if slug else redirect("accounts:login")


class SenhaForm(forms.Form):
    senha1 = forms.CharField(label="Nova senha", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    senha2 = forms.CharField(label="Confirmar senha", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))


class ConviteView(View):
    """Responsável define a própria senha pelo link de uso único."""

    def _acesso(self, token):
        a = AcessoResponsavel.por_convite(token)
        if a is None or not a.liberado or not ConfiguracaoPortal.objects.filter(
                instituicao=a.instituicao, ativo=True).exists():
            return None
        return a

    def get(self, request, token):
        a = self._acesso(token)
        if a is None:
            return _sem_cache(render(request, "portal/convite_invalido.html", status=404))
        return _sem_cache(render(request, "portal/convite.html", {"escola": a.instituicao, "form": SenhaForm(), "a": a}))

    def post(self, request, token):
        a = self._acesso(token)
        if a is None:
            return _sem_cache(render(request, "portal/convite_invalido.html", status=404))
        form = SenhaForm(request.POST)
        if form.is_valid():
            s1, s2 = form.cleaned_data["senha1"], form.cleaned_data["senha2"]
            try:
                if s1 != s2:
                    raise ValidationError("As senhas não conferem.")
                sv.validar_senha(s1, a)
            except ValidationError as e:
                form.add_error("senha1", e)
            else:
                a.definir_senha(s1)
                a.save(update_fields=["senha_hash", "convite_hash", "convite_expira"])
                registrar(instituicao=a.instituicao, usuario=None, acao="PORTAL_SENHA_DEFINIDA",
                          entidade="AcessoResponsavel", entidade_id=a.pk,
                          descricao=f"Responsável {a.responsavel.nome} definiu a senha do portal")
                messages.success(request, "Senha criada. Entre com seu CPF e a nova senha.")
                slug = ConfiguracaoPortal.objects.get(instituicao=a.instituicao).slug
                return redirect("portal:login", slug=slug)
        return _sem_cache(render(request, "portal/convite.html", {"escola": a.instituicao, "form": form, "a": a}))


# --------------------------------------------------------------------------- consulta
class InicioView(PortalView):
    def get(self, request):
        from apps.portal.models import AcessoAluno
        flags = {x.aluno_id: x for x in AcessoAluno.objects.filter(acesso=self.acesso)}
        alunos = [(a, flags[a.pk]) for a in sv.alunos_do_acesso(self.acesso)]
        tem_docs = ModeloDocumento.objects.filter(instituicao=self.escola, disponivel_portal=True, ativo=True).exists()
        return self.pagina(request, "portal/inicio.html", alunos=alunos, tem_docs=tem_docs)


class AlunoView(PortalView):
    def get(self, request, pk):
        aluno = self.aluno(pk)
        flag = self.acesso.alunos.get(aluno=aluno)
        mat = b.matriculas_do_aluno(aluno)
        atual = next((m for m in mat if m.status in ("ATIVA", "PENDENTE")), mat[0] if mat else None)
        return self.pagina(request, "portal/aluno.html", aluno=aluno, flag=flag, matricula=atual)


class BoletimView(PortalView):
    def get(self, request, pk):
        aluno = self.aluno(pk, pedagogico=True)
        mats = b.matriculas_do_aluno(aluno)
        escolhida = request.GET.get("matricula")
        m = next((x for x in mats if str(x.pk) == escolhida), None) or (mats[0] if mats else None)
        d = tabela = None
        if m:
            b.garantir_sincronia(m)
            d = b.dados_boletim(m)  # só fases concluídas pelo professor; o restante vem como "não liberado"
            tabela = mark_safe(b.tabela_boletim_html(d)) if d["linhas"] else ""
        return self.pagina(request, "portal/boletim.html", aluno=aluno, matriculas=mats, matricula=m, d=d,
                           tabela=tabela)


class FinanceiroView(PortalView):
    def get(self, request, pk):
        aluno = self.aluno(pk, financeiro=True)
        parcelas = list(self._parcelas(aluno).order_by("vencimento", "numero"))
        boletos = {c.parcela_id: c for c in Cobranca.objects.filter(
            instituicao=self.escola, parcela__in=parcelas, status=Cobranca.Status.PENDENTE, eh_teste=False)}
        hoje = timezone.localdate()
        linhas = [{"p": p, "cobranca": boletos.get(p.pk),
                   "atrasada": p.status in ("ABERTA", "PARCIAL") and p.vencimento < hoje} for p in parcelas]
        return self.pagina(request, "portal/financeiro.html", aluno=aluno, linhas=linhas)

    def _parcelas(self, aluno):
        # só as cobranças EM NOME deste responsável (outro responsável anterior/atual não aparece)
        return (Parcela.objects.filter(instituicao=self.escola, aluno=aluno, responsavel=self.acesso.responsavel)
                .exclude(status="CANCELADA"))


class BoletoView(PortalView):
    def get(self, request, pk, parcela_pk):
        aluno = self.aluno(pk, financeiro=True)
        parcela = get_object_or_404(Parcela.objects.filter(instituicao=self.escola, aluno=aluno,
                                                           responsavel=self.acesso.responsavel)
                                    .exclude(status="CANCELADA"), pk=parcela_pk)
        cobranca = Cobranca.objects.filter(instituicao=self.escola, parcela=parcela, status=Cobranca.Status.PENDENTE,
                                           eh_teste=False).first()
        if cobranca is None:
            raise Http404
        registrar(instituicao=self.escola, usuario=None, acao="PORTAL_BOLETO_VISTO", entidade="Cobranca",
                  entidade_id=cobranca.pk, aluno_id=aluno.pk,
                  descricao=f"Responsável {self.acesso.responsavel.nome} abriu o boleto da parcela {parcela.pk}")
        return self.pagina(request, "portal/boleto.html", aluno=aluno, parcela=parcela, c=cobranca)


# --------------------------------------------------------------------------- documentos
class DocumentosView(PortalView):
    def get(self, request):
        modelos = (ModeloDocumento.objects.filter(instituicao=self.escola, disponivel_portal=True, ativo=True)
                   .exclude(tipo__in=TIPOS_SOMENTE_FICHA).order_by("nome"))
        return self.pagina(request, "portal/documentos.html", modelos=modelos,
                           alunos=list(sv.alunos_do_acesso(self.acesso)),
                           emitidos=DocumentoPortal.objects.filter(acesso=self.acesso).select_related("documento")[:50])

    def post(self, request):
        modelo = ModeloDocumento.objects.filter(
            pk=request.POST.get("modelo") if str(request.POST.get("modelo", "")).isdigit() else 0,
            instituicao=self.escola, disponivel_portal=True, ativo=True).exclude(tipo__in=TIPOS_SOMENTE_FICHA).first()
        if modelo is None:
            raise Http404
        recentes = DocumentoPortal.objects.filter(acesso=self.acesso,
                                                  criado_em__gte=timezone.now() - timedelta(hours=1)).count()
        if recentes >= LIMITE_DOCUMENTOS_HORA:
            messages.error(self.request, "Limite de documentos por hora atingido. Tente mais tarde.")
            return redirect("portal:documentos")
        aluno = None
        if modelo.destinatario == Destinatario.ALUNO:
            pk = request.POST.get("aluno", "")
            aluno = get_object_or_404(sv.alunos_do_acesso(self.acesso), pk=pk if pk.isdigit() else 0)
        try:
            doc = emitir_documento(modelo=modelo, usuario=None, aluno=aluno, responsavel=self.acesso.responsavel)
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return redirect("portal:documentos")
        DocumentoPortal.objects.create(instituicao=self.escola, acesso=self.acesso, documento=doc)
        registrar(instituicao=self.escola, usuario=None, acao="PORTAL_DOCUMENTO_GERADO", entidade="DocumentoEmitido",
                  entidade_id=doc.pk, aluno_id=aluno.pk if aluno else None,
                  descricao=f"Responsável {self.acesso.responsavel.nome} gerou: {modelo.nome}")
        return redirect("portal:documento", pk=doc.pk)


class DocumentoView(PortalView):
    def get(self, request, pk):
        dp = get_object_or_404(DocumentoPortal.objects.select_related("documento"), documento_id=pk,
                               acesso=self.acesso, instituicao=self.escola)
        return self.pagina(request, "portal/documento.html", doc=dp.documento)
