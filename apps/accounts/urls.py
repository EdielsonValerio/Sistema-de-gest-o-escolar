from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
    path("minha-senha/", views.SenhaTrocarView.as_view(), name="senha_trocar"),
    path("usuarios/", views.UsuarioListView.as_view(), name="usuario_lista"),
    path("usuarios/novo/", views.UsuarioCreateView.as_view(), name="usuario_novo"),
    path("usuarios/<int:pk>/", views.UsuarioUpdateView.as_view(), name="usuario_editar"),
]
