from django.urls import path

from . import views as v
from . import views_gestao as g

app_name = "portal"

urlpatterns = [
    # equipe da escola
    path("configuracoes/portal/", g.ConfiguracaoPortalView.as_view(), name="config"),
    path("responsaveis/<int:pk>/portal/", g.AcessoResponsavelView.as_view(), name="acesso"),
    # responsável
    path("portal/convite/<str:token>/", v.ConviteView.as_view(), name="convite"),
    path("portal/sair/", v.SairView.as_view(), name="sair"),
    path("portal/", v.InicioView.as_view(), name="inicio"),
    path("portal/documentos/", v.DocumentosView.as_view(), name="documentos"),
    path("portal/documentos/<int:pk>/", v.DocumentoView.as_view(), name="documento"),
    path("portal/alunos/<int:pk>/", v.AlunoView.as_view(), name="aluno"),
    path("portal/alunos/<int:pk>/boletim/", v.BoletimView.as_view(), name="boletim"),
    path("portal/alunos/<int:pk>/financeiro/", v.FinanceiroView.as_view(), name="financeiro"),
    path("portal/alunos/<int:pk>/financeiro/<int:parcela_pk>/boleto/", v.BoletoView.as_view(), name="boleto"),
    path("portal/<slug:slug>/", v.LoginView.as_view(), name="login"),
]
