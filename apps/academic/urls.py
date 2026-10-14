from django.urls import path

from . import views as v

app_name = "academic"
urlpatterns = [
    path("anos/", v.AnoLetivoListView.as_view(), name="ano_lista"),
    path("anos/novo/", v.AnoLetivoCreateView.as_view(), name="ano_novo"),
    path("anos/<int:pk>/editar/", v.AnoLetivoUpdateView.as_view(), name="ano_editar"),
    path("series/", v.SerieOfertadaListView.as_view(), name="serie_lista"),
    path("series/nova/", v.SerieOfertadaCreateView.as_view(), name="serie_nova"),
    path("series/<int:pk>/editar/", v.SerieOfertadaUpdateView.as_view(), name="serie_editar"),
    path("series/referencia/", v.SerieReferenciaListView.as_view(), name="serie_ref_lista"),
    path("series/referencia/nova/", v.SerieReferenciaCreateView.as_view(), name="serie_ref_nova"),
    path("series/referencia/<int:pk>/editar/", v.SerieReferenciaUpdateView.as_view(), name="serie_ref_editar"),
    path("turmas/", v.TurmaListView.as_view(), name="turma_lista"),
    path("turmas/nova/", v.TurmaCreateView.as_view(), name="turma_nova"),
    path("turmas/<int:pk>/", v.TurmaDetailView.as_view(), name="turma_detalhe"),
    path("turmas/<int:pk>/editar/", v.TurmaUpdateView.as_view(), name="turma_editar"),
]
