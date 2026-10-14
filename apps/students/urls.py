from django.urls import path

from . import views

app_name = "students"
urlpatterns = [
    path("alunos/", views.AlunoListView.as_view(), name="aluno_lista"),
    path("alunos/novo/", views.AlunoCreateView.as_view(), name="aluno_novo"),
    path("alunos/<int:pk>/", views.AlunoFichaView.as_view(), name="aluno_ficha"),
    path("alunos/<int:pk>/editar/", views.AlunoUpdateView.as_view(), name="aluno_editar"),
    path("responsaveis/", views.ResponsavelListView.as_view(), name="responsavel_lista"),
    path("responsaveis/<int:pk>/", views.ResponsavelDetailView.as_view(), name="responsavel_detalhe"),
    path("responsaveis/novo/", views.ResponsavelCreateView.as_view(), name="responsavel_novo"),
    path("responsaveis/<int:pk>/editar/", views.ResponsavelUpdateView.as_view(), name="responsavel_editar"),
]
