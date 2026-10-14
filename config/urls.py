from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.accounts.urls")),
    path("", include("apps.core.urls")),
    path("", include("apps.students.urls")),
    path("secretaria/", include("apps.academic.urls")),
    path("matriculas/", include("apps.enrollments.urls")),
    path("financeiro/", include("apps.finance.urls")),
    path("auditoria/", include("apps.audit.urls")),
    path("", include("apps.documents.urls")),
]

if settings.DEBUG:  # fotos enviadas (somente desenvolvimento; em produção sirva MEDIA_ROOT pelo servidor web)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
