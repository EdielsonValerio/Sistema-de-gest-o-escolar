from django.urls import path

from . import views

app_name = "payables"
urlpatterns = [
    path("", views.ContaListView.as_view(), name="conta_lista"),
    path("nova/", views.ContaCreateView.as_view(), name="conta_nova"),
    path("<int:pk>/", views.ContaDetailView.as_view(), name="conta_detalhe"),
    path("<int:pk>/editar/", views.ContaUpdateView.as_view(), name="conta_editar"),
    path("<int:pk>/liquidar/", views.ContaLiquidarView.as_view(), name="conta_liquidar"),
    path("<int:pk>/desativar/", views.ContaDesativarView.as_view(), name="conta_desativar"),
    path("<int:pk>/reativar/", views.ContaReativarView.as_view(), name="conta_reativar"),
    path("<int:pk>/estornar-liquidacao/", views.ContaEstornarLiquidacaoView.as_view(), name="conta_estornar"),
]
