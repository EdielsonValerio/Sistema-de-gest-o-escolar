from django.urls import path

from . import views as v

app_name = "enrollments"
urlpatterns = [
    path("", v.MatriculaListView.as_view(), name="lista"),
    path("aluno/<int:pk>/vincular/", v.VincularTurmaView.as_view(), name="vincular"),
    path("<int:pk>/cobrancas/", v.GerarCobrancasView.as_view(), name="cobrancas"),
    path("<int:pk>/<str:acao>/", v.MatriculaAcaoView.as_view(), name="acao"),
]
