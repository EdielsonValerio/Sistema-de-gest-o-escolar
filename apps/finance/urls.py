from django.urls import path

from . import views as v

app_name = "finance"
urlpatterns = [
    path("planos/", v.PlanoListView.as_view(), name="plano_lista"),
    path("planos/novo/", v.PlanoCreateView.as_view(), name="plano_novo"),
    path("planos/<int:pk>/", v.PlanoDetailView.as_view(), name="plano_detalhe"),
    path("planos/<int:pk>/editar/", v.PlanoUpdateView.as_view(), name="plano_editar"),
    path("contas-a-receber/", v.ParcelaListView.as_view(), name="parcela_lista"),
    path("contas-a-receber/<int:pk>/", v.ParcelaDetailView.as_view(), name="parcela_detalhe"),
    path("contas-a-receber/<int:pk>/ajustar/", v.ParcelaAjustarView.as_view(), name="parcela_ajustar"),
    path("caixa/", v.CaixaBuscaView.as_view(), name="caixa"),
    path("caixa/aluno/<int:pk>/", v.CaixaAlunoView.as_view(), name="caixa_aluno"),
    path("recebimentos/<int:pk>/", v.RecebimentoDetailView.as_view(), name="recebimento_detalhe"),
    path("recebimentos/<int:pk>/estornar/", v.RecebimentoEstornarView.as_view(), name="recebimento_estornar"),
    path("relatorios/recebimentos/", v.RelatorioRecebimentosView.as_view(), name="relatorio_recebimentos"),
    path("relatorios/estornos/", v.RelatorioEstornosView.as_view(), name="relatorio_estornos"),
]
