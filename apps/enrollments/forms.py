from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.core.mixins import BaseForm
from apps.finance.models import PlanoFinanceiro
from apps.students.models import Responsavel


class VinculoForm(BaseForm):
    """Confirmação do vínculo (e, opcionalmente, da contratação de plano)."""

    data_ingresso = forms.DateField(label="Data de ingresso", initial=timezone.localdate)
    plano = forms.ModelChoiceField(label="Plano financeiro", queryset=PlanoFinanceiro.objects.none(), required=False,
                                   empty_label="— sem cobranças —")
    responsavel = forms.ModelChoiceField(label="Responsável financeiro", queryset=Responsavel.objects.none(),
                                         required=False)
    sem_cobranca = forms.BooleanField(label="Concluir sem gerar cobranças", required=False)

    def __init__(self, *args, turma=None, aluno=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.turma, self.aluno = turma, aluno
        self.fields["plano"].queryset = PlanoFinanceiro.objects.filter(
            turma=turma, situacao=PlanoFinanceiro.Situacao.ATIVO) if turma else PlanoFinanceiro.objects.none()
        self.fields["responsavel"].queryset = Responsavel.objects.filter(instituicao=self.instituicao, ativo=True)
        if aluno is not None and not self.is_bound:
            atual = aluno.responsavel_financeiro_atual
            if atual:
                self.fields["responsavel"].initial = atual.pk

    def clean(self):
        d = super().clean()
        if d.get("plano") and not d.get("responsavel"):
            self.add_error("responsavel", "Informe o responsável financeiro para gerar as cobranças.")
        return d


class CobrancaForm(VinculoForm):
    """Geração de cobranças para uma matrícula já existente (plano e responsável obrigatórios)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome in ("data_ingresso", "sem_cobranca"):
            self.fields.pop(nome)
        self.fields["plano"].required = True
        self.fields["plano"].empty_label = "— selecione —"
        self.fields["responsavel"].required = True


class JustificativaForm(BaseForm):
    justificativa = forms.CharField(label="Justificativa", max_length=255, widget=forms.Textarea(attrs={"rows": 2}))
    data = forms.DateField(label="Data", initial=timezone.localdate)


class TransferenciaForm(JustificativaForm):
    nova_turma = forms.ModelChoiceField(label="Turma de destino", queryset=None)

    def __init__(self, *args, matricula=None, **kwargs):
        super().__init__(*args, **kwargs)
        from apps.academic.models import Turma
        self.fields["nova_turma"].queryset = (
            Turma.objects.filter(instituicao=self.instituicao, tipo="OFICIAL", ano_letivo=matricula.ano_letivo)
            .exclude(situacao="ENCERRADA").exclude(pk=matricula.turma_id).order_by("nome"))
        self.order_fields(["nova_turma", "data", "justificativa"])


class CancelamentoForm(JustificativaForm):
    cancelar_parcelas = forms.BooleanField(
        label="Cancelar também as parcelas em aberto (sem pagamento)", required=False)


class ConclusaoForm(BaseForm):
    data = forms.DateField(label="Data de conclusão", initial=timezone.localdate)
