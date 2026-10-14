from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("busca/", views.BuscaRapidaView.as_view(), name="busca"),
    path("configuracoes/", views.ConfiguracoesGeraisView.as_view(), name="configuracoes"),
    path("configuracoes/numeracao/", views.NumeracaoListView.as_view(), name="numeracao_lista"),
    path("configuracoes/numeracao/<int:pk>/", views.NumeracaoEditView.as_view(), name="numeracao_editar"),
    path("configuracoes/numeracao/<int:pk>/previa/", views.NumeracaoPreviaView.as_view(), name="numeracao_previa"),
]
