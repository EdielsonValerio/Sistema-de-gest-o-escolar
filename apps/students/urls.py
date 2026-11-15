from django.urls import path

from . import views, views_prematricula as pm

app_name = "students"
urlpatterns = [
    path("alunos/", views.AlunoListView.as_view(), name="aluno_lista"),
    path("alunos/novo/", views.AlunoCreateView.as_view(), name="aluno_novo"),
    path("alunos/<int:pk>/", views.AlunoFichaView.as_view(), name="aluno_ficha"),
    path("alunos/<int:pk>/exportar-dados/", views.AlunoExportarDadosView.as_view(), name="aluno_exportar_dados"),
    path("alunos/<int:pk>/foto/", views.AlunoFotoView.as_view(), name="aluno_foto"),
    path("alunos/<int:pk>/editar/", views.AlunoUpdateView.as_view(), name="aluno_editar"),
    path("responsaveis/", views.ResponsavelListView.as_view(), name="responsavel_lista"),
    path("responsaveis/<int:pk>/", views.ResponsavelDetailView.as_view(), name="responsavel_detalhe"),
    path("responsaveis/novo/", views.ResponsavelCreateView.as_view(), name="responsavel_novo"),
    path("responsaveis/<int:pk>/editar/", views.ResponsavelUpdateView.as_view(), name="responsavel_editar"),
    path("configuracoes/pre-matricula/", pm.PreMatriculaConfigView.as_view(), name="pre_matricula_config"),
    path("pre-matricula/<str:token>/", pm.PreMatriculaPublicaView.as_view(), name="pre_matricula_publica"),
    path("pre-matricula/<str:token>/concluido/", pm.PreMatriculaConcluidaView.as_view(),
         name="pre_matricula_concluida"),
]
