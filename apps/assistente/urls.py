from django.urls import path

from . import views

app_name = "assistente"
urlpatterns = [
    path("assistente/", views.ChatView.as_view(), name="chat"),
    path("assistente/perguntar/", views.PerguntarView.as_view(), name="perguntar"),
    path("assistente/conversas/<int:pk>/excluir/", views.ExcluirConversaView.as_view(), name="conversa_excluir"),
    path("configuracoes/assistente-ia/", views.ConfiguracaoView.as_view(), name="configuracao"),
]
