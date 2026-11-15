from django.urls import path

from . import views_coord as c
from . import views_doc as d
from . import views_prof as p
from . import views_rel as r
from . import views_sistema as s

app_name = "diario"
urlpatterns = [
    # professor
    path("diario/", p.PainelProfessorView.as_view(), name="painel"),
    path("diario/turma/<int:pk>/", p.DiarioView.as_view(), name="diario"),
    path("diario/turma/<int:pk>/fechar/<int:periodo_pk>/", p.FecharPeriodoView.as_view(), name="fechar_periodo"),
    path("diario/turma/<int:pk>/aulas/", p.AulaListView.as_view(), name="aulas"),
    path("diario/turma/<int:pk>/aulas/nova/", p.AulaFormView.as_view(), name="aula_nova"),
    path("diario/turma/<int:pk>/aulas/<int:aula_pk>/", p.AulaFormView.as_view(), name="aula_editar"),
    path("diario/turma/<int:pk>/frequencia/", p.FrequenciaResumoView.as_view(), name="frequencia"),
    path("diario/turma/<int:pk>/avaliacoes/", p.AvaliacaoListView.as_view(), name="avaliacoes"),
    path("diario/turma/<int:pk>/avaliacoes/nova/", p.AvaliacaoCreateView.as_view(), name="avaliacao_nova"),
    path("diario/turma/<int:pk>/avaliacoes/<int:av_pk>/notas/", p.NotasView.as_view(), name="notas"),
    path("diario/turma/<int:pk>/desempenho/", p.DesempenhoView.as_view(), name="desempenho"),
    path("diario/turma/<int:pk>/planejamento/", p.PlanejamentoListView.as_view(), name="planejamentos"),
    path("diario/turma/<int:pk>/planejamento/novo/", p.PlanejamentoFormView.as_view(), name="planejamento_novo"),
    path("diario/turma/<int:pk>/planejamento/<int:plano_pk>/", p.PlanejamentoFormView.as_view(), name="planejamento_editar"),
    # coordenação
    path("coordenacao/", c.PainelCoordenacaoView.as_view(), name="coordenacao"),
    path("coordenacao/alocacoes/", c.AlocacaoListView.as_view(), name="alocacoes"),
    path("coordenacao/alocacoes/nova/", c.AlocacaoCreateView.as_view(), name="alocacao_nova"),
    path("coordenacao/alocacoes/<int:pk>/encerrar/", c.AlocacaoEncerrarView.as_view(), name="alocacao_encerrar"),
    path("coordenacao/alocacoes/<int:pk>/trocar/", c.AlocacaoTrocarView.as_view(), name="alocacao_trocar"),
    path("coordenacao/disciplinas/", c.DisciplinaListView.as_view(), name="disciplinas"),
    path("coordenacao/disciplinas/nova/", c.DisciplinaCreateView.as_view(), name="disciplina_nova"),
    path("coordenacao/disciplinas/<int:pk>/", c.DisciplinaUpdateView.as_view(), name="disciplina_editar"),
    path("coordenacao/periodos/", c.PeriodoListView.as_view(), name="periodos"),
    path("coordenacao/periodos/novo/", c.PeriodoCreateView.as_view(), name="periodo_novo"),
    path("coordenacao/periodos/<int:pk>/", c.PeriodoUpdateView.as_view(), name="periodo_editar"),
    path("coordenacao/periodos/bimestres/<int:ano_pk>/", c.CriarBimestresView.as_view(), name="criar_bimestres"),
    path("coordenacao/aulas/", c.AulasAcompanhamentoView.as_view(), name="aulas_coordenacao"),
    path("coordenacao/aulas/<int:pk>/correcao/", c.SolicitarCorrecaoView.as_view(), name="solicitar_correcao"),
    path("coordenacao/planejamentos/", c.PlanejamentosCoordenacaoView.as_view(), name="planejamentos_coordenacao"),
    path("coordenacao/pendencias/", c.PendenciasView.as_view(), name="pendencias"),
    path("coordenacao/fechamento/", c.FechamentoView.as_view(), name="fechamento"),
    path("coordenacao/fechamento/<int:pk>/reabrir/", c.ReabrirDiarioView.as_view(), name="reabrir"),
    # parametrização da avaliação (somente administrador do sistema, em modo suporte)
    path("sistema/avaliacao/", s.ModeloListView.as_view(), name="modelos"),
    path("sistema/avaliacao/regra-padrao/", s.RegraPadraoView.as_view(), name="regra_padrao"),
    path("sistema/avaliacao/novo/", s.ModeloFormView.as_view(editando=False), name="modelo_novo"),
    path("sistema/avaliacao/<int:pk>/", s.ModeloDetalheView.as_view(), name="modelo"),
    path("sistema/avaliacao/<int:pk>/editar/", s.ModeloFormView.as_view(editando=True), name="modelo_editar"),
    path("sistema/avaliacao/<int:pk>/gerar-fases/", s.GerarFasesView.as_view(), name="modelo_gerar_fases"),
    path("sistema/avaliacao/<int:pk>/publicar/", s.PublicarModeloView.as_view(), name="modelo_publicar"),
    path("sistema/avaliacao/<int:pk>/despublicar/", s.DespublicarModeloView.as_view(), name="modelo_despublicar"),
    path("sistema/avaliacao/<int:pk>/duplicar/", s.DuplicarModeloView.as_view(), name="modelo_duplicar"),
    path("sistema/avaliacao/<int:pk>/simulador/", s.SimuladorView.as_view(), name="modelo_simulador"),
    path("sistema/avaliacao/<int:pk>/fases/nova/", s.FaseFormView.as_view(editando=False), name="fase_nova"),
    path("sistema/avaliacao/<int:pk>/fases/<int:fase_pk>/", s.FaseFormView.as_view(editando=True), name="fase_editar"),
    path("sistema/avaliacao/<int:pk>/fases/<int:fase_pk>/excluir/", s.FaseExcluirView.as_view(), name="fase_excluir"),
    path("sistema/avaliacao/<int:pk>/fases/<int:fase_pk>/componentes/novo/", s.ComponenteFormView.as_view(editando=False), name="componente_novo"),
    path("sistema/avaliacao/<int:pk>/fases/<int:fase_pk>/componentes/<int:comp_pk>/", s.ComponenteFormView.as_view(editando=True), name="componente_editar"),
    path("sistema/avaliacao/<int:pk>/fases/<int:fase_pk>/componentes/<int:comp_pk>/excluir/", s.ComponenteExcluirView.as_view(), name="componente_excluir"),
    # relatórios
    path("diario/relatorios/", r.RelatoriosView.as_view(), name="relatorios"),
    path("diario/relatorios/<slug:codigo>/", r.RelatorioView.as_view(), name="relatorio"),
    path("diario/boletim/<int:matricula_pk>/", r.BoletimView.as_view(), name="boletim"),
    path("alunos/<int:pk>/boletim/", d.BoletimAlunoView.as_view(), name="boletim_aluno"),
    path("alunos/<int:pk>/historico/", d.HistoricoAlunoView.as_view(), name="historico_aluno"),
    path("alunos/<int:pk>/historico/externo/novo/", d.HistoricoExternoView.as_view(), name="historico_externo_novo"),
    path("alunos/<int:pk>/historico/externo/<int:ext_pk>/", d.HistoricoExternoView.as_view(),
         name="historico_externo_editar"),
    path("alunos/<int:pk>/historico/externo/<int:ext_pk>/excluir/", d.HistoricoExternoExcluirView.as_view(),
         name="historico_externo_excluir"),
]
