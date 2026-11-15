from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from django.shortcuts import redirect

# O login do Django admin não tem limite de tentativas nem MFA: redireciona para o login do sistema.
admin.site.login = lambda request, extra_context=None: redirect("/entrar/?sistema=1")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.students.urls")),
    path("secretaria/", include("apps.academic.urls")),
    path("matriculas/", include("apps.enrollments.urls")),
    path("financeiro/contas-a-pagar/", include("apps.payables.urls")),
    path("financeiro/", include("apps.finance.urls")),
    path("auditoria/", include("apps.audit.urls")),
    path("", include("apps.documents.urls")),
    path("", include("apps.billing.urls")),
    path("", include("apps.assistente.urls")),
    path("", include("apps.diario.urls")),
]

if settings.DEBUG:
    # Desenvolvimento: só os logotipos são públicos (aparecem na tela de login). Fotos de alunos NÃO são servidas
    # como arquivo: só pela tela autenticada (students:aluno_foto). Em produção, o nginx deve expor apenas /media/logotipos/.
    from django.urls import re_path
    from django.views.static import serve

    urlpatterns += [re_path(r"^%s(?P<path>logotipos/.*)$" % settings.MEDIA_URL.lstrip("/"), serve,
                            {"document_root": settings.MEDIA_ROOT})]
