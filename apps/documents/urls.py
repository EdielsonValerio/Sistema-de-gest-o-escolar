from django.urls import path

from . import views

app_name = "documents"
urlpatterns = [
    path("configuracoes/documentos/", views.ModeloListView.as_view(), name="modelo_lista"),
    path("configuracoes/documentos/novo/", views.ModeloCreateView.as_view(), name="modelo_novo"),
    path("configuracoes/documentos/<int:pk>/", views.ModeloUpdateView.as_view(), name="modelo_editar"),
    path("configuracoes/documentos/previa/", views.PreviaView.as_view(), name="previa"),
    path("configuracoes/documentos/cabecalho/", views.CabecalhoView.as_view(), name="cabecalho"),
    path("documentos/aluno/<int:pk>/", views.EmitirAlunoView.as_view(), name="emitir_aluno"),
    path("documentos/responsavel/<int:pk>/", views.EmitirResponsavelView.as_view(), name="emitir_responsavel"),
    path("documentos/emitidos/<int:pk>/", views.DocumentoView.as_view(), name="documento"),
]
