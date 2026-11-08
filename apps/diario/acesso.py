"""Autorização do Diário no backend: perfil + escola + situação + alocação. Nada depende de ocultar menu."""
from django.shortcuts import get_object_or_404
from django.utils.functional import cached_property

from apps.core.mixins import AcessoMixin

from .models import AlocacaoProfessor


class ProfessorMixin(AcessoMixin):
    """Ambiente do professor: só alocações DELE, da escola dele. Alocação de outro professor/escola = 404."""

    permission_required = "diario.usar_diario_professor"

    def has_permission(self):
        return super().has_permission() and self.request.user.is_active

    @cached_property
    def alocacao(self):
        return get_object_or_404(AlocacaoProfessor.objects.select_related("turma", "disciplina", "ano_letivo", "professor"),
                                 pk=self.kwargs["pk"], instituicao=self.instituicao, professor=self.request.user)


class CoordenacaoMixin(AcessoMixin):
    permission_required = "diario.coordenar_diario"
