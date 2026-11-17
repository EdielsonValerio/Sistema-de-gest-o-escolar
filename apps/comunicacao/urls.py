from django.urls import path

from . import views as v

app_name = "comunicacao"
urlpatterns = [
    path("configuracoes/whatsapp/", v.ConfiguracaoView.as_view(), name="config"),
    path("comunicados/", v.ListaView.as_view(), name="lista"),
    path("comunicados/novo/", v.NovoView.as_view(), name="novo"),
    path("comunicados/<int:pk>/", v.DetalheView.as_view(), name="detalhe"),
]
