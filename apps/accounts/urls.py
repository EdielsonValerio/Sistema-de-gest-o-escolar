from django.urls import path

from . import views, views_seguranca as vs

app_name = "accounts"
urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
    path("minha-senha/", views.SenhaTrocarView.as_view(), name="senha_trocar"),
    path("recuperar-senha/", vs.RecuperarSenhaView.as_view(), name="recuperar"),
    path("redefinir-senha/<str:token>/", vs.DefinirSenhaPorTokenView.as_view(), name="redefinir"),
    path("ativar-conta/<str:token>/", vs.AtivarContaView.as_view(), name="ativar"),
    path("mfa/verificar/", vs.MfaVerificarView.as_view(), name="mfa_verificar"),
    path("mfa/configurar/", vs.MfaConfigurarView.as_view(), name="mfa_configurar"),
    path("usuarios/<int:pk>/mfa/redefinir/", vs.MfaRedefinirView.as_view(), name="mfa_redefinir"),
    path("usuarios/", views.UsuarioListView.as_view(), name="usuario_lista"),
    path("usuarios/novo/", views.UsuarioCreateView.as_view(), name="usuario_novo"),
    path("usuarios/<int:pk>/", views.UsuarioUpdateView.as_view(), name="usuario_editar"),
]
