from django.urls import path

from . import views

app_name = "billing"
urlpatterns = [
    path("configuracoes/cobranca/", views.ConfiguracaoCobrancaView.as_view(), name="configuracao"),
    path("financeiro/cobrancas/", views.CobrancaListView.as_view(), name="cobranca_lista"),
    path("financeiro/cobrancas/<int:pk>/boleto/", views.BoletoView.as_view(), name="boleto"),
    path("financeiro/cobrancas/<int:pk>/consultar/", views.CobrancaConsultarView.as_view(), name="cobranca_consultar"),
    path("financeiro/cobrancas/<int:pk>/cancelar/", views.CobrancaCancelarView.as_view(), name="cobranca_cancelar"),
    path("financeiro/cobrancas/parcela/<int:pk>/gerar/", views.CobrancaGerarParcelaView.as_view(), name="cobranca_gerar"),
    path("webhooks/cobranca/<uuid:slug>/", views.WebhookView.as_view(), name="webhook"),
]
