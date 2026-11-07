from django.urls import path

from . import views, views_escolas

app_name = "core"
urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("busca/", views.BuscaRapidaView.as_view(), name="busca"),
    path("configuracoes/", views.ConfiguracoesGeraisView.as_view(), name="configuracoes"),
    path("configuracoes/numeracao/", views.NumeracaoListView.as_view(), name="numeracao_lista"),
    path("configuracoes/numeracao/<int:pk>/", views.NumeracaoEditView.as_view(), name="numeracao_editar"),
    # Administrador do sistema (superusuário)
    path("sistema/escolas/", views_escolas.EscolaListView.as_view(), name="escola_lista"),
    path("sistema/escolas/nova/", views_escolas.EscolaCreateView.as_view(), name="escola_nova"),
    path("sistema/escolas/<int:pk>/", views_escolas.EscolaUpdateView.as_view(), name="escola_editar"),
    path("sistema/escolas/<int:pk>/acessar/", views_escolas.EscolaAcessarView.as_view(), name="escola_acessar"),
    path("sistema/sair-da-escola/", views_escolas.EscolaSairView.as_view(), name="escola_sair"),
    path("sistema/escolas/<int:pk>/administradores/novo/", views_escolas.AdministradorCreateView.as_view(),
         name="escola_admin_novo"),
    path("sistema/escolas/<int:pk>/administradores/<int:user_pk>/senha/",
         views_escolas.AdministradorSenhaView.as_view(), name="escola_admin_senha"),
    path("configuracoes/numeracao/<int:pk>/previa/", views.NumeracaoPreviaView.as_view(), name="numeracao_previa"),
]
