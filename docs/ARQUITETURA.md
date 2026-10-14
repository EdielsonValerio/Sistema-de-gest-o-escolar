# Arquitetura e Modelagem — MVP de Gestão Escolar

## 1. Análise resumida dos requisitos

Sistema web (Django + Bootstrap 5) para Secretaria e Financeiro de uma escola. Três ideias estruturam tudo:

1. **Aluno ≠ vínculo educacional.** O cadastro do aluno é permanente; matrícula/inscrição é uma entidade própria (`Matricula`).
2. **Aluno ≠ responsável financeiro ≠ plano ≠ parcela.** Cada um é uma entidade; a parcela guarda *snapshots* para o histórico não mudar retroativamente.
3. **Dinheiro é imutável e auditável.** Recebimento nunca é apagado; estorno é um registro que reverte alocações.

### Ambiguidades e premissas adotadas

| # | Ambiguidade | Premissa adotada |
|---|---|---|
| P1 | Quem pode "editar valores" (§5.4: "responsável financeiro autorizado") | Interpretado como **usuário com perfil Financeiro e permissão específica** (`aplicar_desconto`, `alterar_vencimento`, `corrigir_valor`) |
| P2 | Pagamento acima do saldo / adiantamento | **Não permitido**. Valor alocado ≤ saldo da parcela. Excedente em dinheiro vira **troco** (nunca receita) |
| P3 | Pagamento parcial de uma parcela | Operador informa, por parcela, o valor a receber (padrão = saldo). Alocação parcial mantém `PARCIAL` |
| P4 | Multa/juros | Campos no plano (`multa_percentual`, `juros_mensal_percentual`, `aplicar_encargos`). No MVP o encargo é **calculado na tela de caixa por dia de atraso (juros simples)** e gravado na parcela no momento do recebimento como `acrescimo`, com auditoria. Padrão: desabilitado |
| P5 | Situação "Vencida" | **Derivada**, não armazenada: `status ∈ {ABERTA, PARCIAL}` e `vencimento < hoje`. Evita jobs agendados e inconsistência. Filtros usam essa regra via `QuerySet` |
| P6 | Duas matrículas oficiais no mesmo ano | Proibido por **constraint parcial única** (`aluno + ano_letivo` com `tipo=OFICIAL` e status em PENDENTE/ATIVA) e validado no serviço. **Não há flag para permitir exceção** no MVP (ver limitações) |
| P7 | Escopo de numeração | `INSTITUICAO`, `ANO_LETIVO`, `SERIE`, `TURMA`. A chave do contador é derivada do escopo |
| P8 | Códigos MEC | **Não são inventados.** A tabela `SerieReferencia` traz etapas/séries da estrutura da LDB (Educação Infantil, EF I, EF II, EM) com códigos **internos** estáveis (`EI-CRECHE`, `EF1-01`…). Campo `codigo_oficial` fica vazio e editável |
| P9 | Multi-instituição | `Instituicao` existe e as entidades de domínio têm FK `instituicao`. Um *manager* e *mixin* filtram pela instituição do usuário (`PerfilUsuario.instituicao`). Sem multitenancy completo |
| P10 | Idempotência de recebimentos | Token de idempotência (UUID) gerado ao abrir o formulário; `Recebimento.token_idempotencia` é **único**. Reenvio retorna o recebimento já criado |
| P11 | Idempotência da geração de parcelas | `ContratoFinanceiro` tem **OneToOne com a matrícula** (uma contratação ativa por matrícula) + constraint única `(matricula, parcela)` |
| P12 | Responsável "próprio aluno" | `Responsavel` é criado/vinculado com `eh_proprio_aluno=True` copiando dados do aluno no momento do vínculo (snapshot), sem exigir redigitação |
| P13 | Banco | SQLite por padrão (sem configuração); PostgreSQL via `DATABASE_URL`. Código usa `select_for_update` (efetivo no PostgreSQL; no SQLite as transações são serializadas por escrita) |
| P14 | Foto do aluno | `ImageField` opcional (Pillow) |
| P15 | Exclusão | Sem exclusão física de alunos, matrículas, parcelas, recebimentos. `on_delete=PROTECT` em tudo que tem histórico |

## 2. Decisões de arquitetura

- **Apps Django** (6, sem fragmentação excessiva):
  - `core` — instituição, configurações gerais, numeração (config + sequência + serviço), mixins, dashboard, pesquisa rápida
  - `accounts` — perfil do usuário, grupos/permissões iniciais, gestão de usuários
  - `students` — `Aluno`, `Responsavel`, `AlunoResponsavelFinanceiro` (histórico)
  - `academic` — `AnoLetivo`, `SerieReferencia`, `SerieOfertada`, `Turma`
  - `enrollments` — `Matricula` (vínculo), `HistoricoMatricula`; serviços de matrícula/transferência
  - `finance` — `PlanoFinanceiro`, `ContratoFinanceiro`, `Parcela`, `AjusteParcela`, `Recebimento`, `AlocacaoRecebimento`, `Estorno`; serviços
  - `audit` — `RegistroAuditoria` (único mecanismo de histórico; as abas "Histórico" consultam-no)
- **Regras críticas em `services.py`** com `transaction.atomic()`, nunca em views/templates.
- **Constraints de banco** para integridade (unicidade, `CheckConstraint` de valores ≥ 0, datas, tipo/série coerentes).
- **Permissões**: permissões customizadas do Django (`Meta.permissions`) agrupadas em 4 grupos criados por *data migration* (Administrador, Secretaria, Financeiro, Consulta). Views usam `PermissionRequiredMixin`; o menu usa `perms` apenas para exibição.
- **Auditoria**: `audit.services.registrar(...)` grava usuário, ação, entidade, id e um JSON com **apenas campos não sensíveis** (ids, valores, status — nunca CPF, senha, endereço).
- **Dinheiro**: `DecimalField(max_digits=12, decimal_places=2)`, `Decimal` com `ROUND_HALF_UP`; sem `float`.
- **Frontend**: Django Templates + Bootstrap 5 + Bootstrap Icons por CDN; JS vanilla para máscaras, troco e seleção de parcelas; HTMX **não** utilizado (desnecessário).
- **Idioma/Locale**: `pt-br`, `America/Fortaleza`, `USE_THOUSAND_SEPARATOR`.

## 3. Modelo de dados

### 3.1 Relacionamentos e cardinalidades

```
Instituicao 1───N (Aluno, Responsavel, AnoLetivo, Turma, ConfiguracaoNumeracao, PlanoFinanceiro, Recebimento...)
User 1───1 PerfilUsuario N───1 Instituicao

Aluno 1───N AlunoResponsavelFinanceiro N───1 Responsavel      (histórico; no máx. 1 vigente por aluno)
Aluno 1───N Matricula N───1 Turma
AnoLetivo 1───N SerieOfertada N───1 SerieReferencia
SerieOfertada 1───N Turma   (somente turmas OFICIAIS)
AnoLetivo 1───N Turma       (turmas oficiais)
Matricula 1───N HistoricoMatricula (via auditoria + auto-FK `matricula_anterior` para transferência)

Turma 1───N PlanoFinanceiro
PlanoFinanceiro 1───N ContratoFinanceiro N───1 Matricula   (OneToOne por matrícula ativa)
ContratoFinanceiro 1───N Parcela N───1 Aluno, N───1 Responsavel (snapshot do responsável na geração)
Parcela 1───N AjusteParcela
Recebimento 1───N AlocacaoRecebimento N───1 Parcela
Recebimento 1───0..1 Estorno
```

### 3.2 Entidades principais (PK sempre `id` BigAutoField)

**core.Instituicao** — `nome`, `cnpj`(opt), `ativa`.
**core.ConfiguracaoNumeracao** — `instituicao`, `tipo` (CODIGO_ALUNO/MATRICULA_OFICIAL/INSCRICAO_NAO_OFICIAL), `formato` (ex.: `[AnoLetivo][NumeroSerie][NumeroTurma][Sequencial]`), `separador`, `tamanho_sequencial`, `escopo`, `ativa`, `gerar_inscricao_nao_oficial`. Único `(instituicao, tipo)`.
**core.SequenciaNumeracao** — `instituicao`, `tipo`, `chave_escopo`, `ultimo_valor`. Único `(instituicao, tipo, chave_escopo)`. Incrementada com `select_for_update` + `F()` dentro de transação; criação protegida com `get_or_create` + retry em `IntegrityError`.
**students.Aluno** — `instituicao`, `codigo` (único por instituição, imutável), dados de identificação/contato/endereço/filiação, `ativo`, `criado_por`, `criado_em`. Índices em `nome`, `cpf`, `nome_mae`. CPF único por instituição quando preenchido (constraint condicional).
**students.Responsavel** — `instituicao`, `nome`, `documento` (CPF/CNPJ), `tipo_pessoa`, `email_cobranca`, `telefone`, endereço de cobrança, `eh_proprio_aluno`, `ativo`.
**students.AlunoResponsavelFinanceiro** — `aluno`, `responsavel`, `parentesco`, `inicio`, `fim`(null=vigente), `criado_por`. Constraint: **um vigente por aluno** (`UniqueConstraint(aluno, condition=fim IS NULL)`).
**academic.AnoLetivo** — `instituicao`, `nome`, `ano`, `data_inicio`, `data_fim`, `situacao` (PLANEJAMENTO/ATIVO/ENCERRADO). Único `(instituicao, ano)`; check `data_fim >= data_inicio`.
**academic.SerieReferencia** — `codigo` (único, interno), `nome`, `etapa`, `ordem`, `codigo_oficial` (opt), `ativa`.
**academic.SerieOfertada** — `ano_letivo`, `serie_referencia`, `situacao`, `observacoes`. Único `(ano_letivo, serie_referencia)`.
**academic.Turma** — `instituicao`, `codigo` (único, interno, imutável), `numero` (identificador numérico estável, único por escopo), `nome`, `tipo` (OFICIAL/NAO_OFICIAL), `ano_letivo`+`serie_ofertada` (oficial), `ano_referencia` (não oficial), `data_inicio`, `data_fim`, `categoria`, `capacidade`, `situacao`, `exige_numero_matricula`. Constraints: check de datas; check de coerência por tipo (oficial ⇒ serie_ofertada e ano_letivo não nulos; não oficial ⇒ ambos nulos, `ano_referencia`, `data_inicio` e `data_fim` não nulos). Validação adicional: série pertence ao mesmo ano letivo.
**enrollments.Matricula** — `instituicao`, `aluno`, `turma`, `tipo` (denormalizado de turma para constraint), `ano_letivo` (null p/ não oficial), `numero` (null se não gerado), `status` (PENDENTE/ATIVA/TRANSFERIDA/CANCELADA/CONCLUIDA), `data_ingresso`, `data_saida`, `matricula_anterior` (self-FK), `criado_por`. Constraints: **única parcial** `(aluno, ano_letivo)` para `tipo=OFICIAL ∧ status ∈ (PENDENTE, ATIVA)`; único `(instituicao, numero)` quando preenchido; único `(aluno, turma)` para status ativos.
**finance.PlanoFinanceiro** — `instituicao`, `nome`, `turma`, `valor_total`, `quantidade_parcelas`, `valor_parcela` (calculado e gravado), `dia_vencimento`, `data_primeira_parcela`, `desconto_padrao`, `multa_percentual`, `juros_mensal_percentual`, `aplicar_encargos`, `situacao`, `observacoes`. Checks: valores ≥ 0, parcelas ≥ 1, dia 1–28.
**finance.ContratoFinanceiro** — `matricula` (OneToOne, PROTECT), `plano`, `responsavel`, snapshots (`plano_nome`, `valor_total`, `quantidade_parcelas`), `status` (ATIVO/CANCELADO), `criado_por`.
**finance.Parcela** — `instituicao`, `aluno`, `matricula`, `contrato`, `responsavel`, `numero`, `descricao`, `valor_original`, `desconto`, `acrescimo`, `valor_devido` (= original − desconto + acréscimo, mantido pelo serviço e protegido por check), `valor_pago`, `emissao`, `vencimento`, `quitacao`, `status` (ABERTA/PARCIAL/QUITADA/CANCELADA). Único `(contrato, numero)`. Checks: `desconto ≤ valor_original`, `valor_pago ≤ valor_devido`, valores ≥ 0. Propriedades: `saldo`, `esta_vencida`.
**finance.AjusteParcela** — trilha de alterações de valor/vencimento: `parcela`, `tipo`, valores anterior/posterior, `justificativa`, `usuario`, `criado_em`.
**finance.Recebimento** — `instituicao`, `aluno`, `responsavel`, `numero` (sequência própria), `forma` (DINHEIRO/PIX/CREDITO/DEBITO), `valor_total` (soma das alocações), `valor_recebido`, `troco`, `referencia`, `status` (CONFIRMADO/ESTORNADO), `token_idempotencia` (único), `operador`, `criado_em`. Checks: `troco = valor_recebido − valor_total` apenas em dinheiro; `troco = 0` nas demais.
**finance.AlocacaoRecebimento** — `recebimento`, `parcela`, `valor` (> 0), `estornada`.
**finance.Estorno** — `recebimento` (OneToOne), `justificativa`, `usuario`, `criado_em`.
**audit.RegistroAuditoria** — `instituicao`, `usuario`, `acao`, `entidade`, `entidade_id`, `aluno_id` (opt, p/ aba de histórico), `dados` (JSON), `criado_em`. Índices em `(entidade, entidade_id)`, `aluno_id`, `criado_em`. Sem update/delete pela aplicação.

### 3.3 Estratégia de geração de códigos

- **Código do aluno**: formato `[AnoCadastro][Sequencial]`, sequência por `AnoCadastro` (escopo ano), preenchida com zeros (tamanho configurável).
- **Matrícula**: substituição de variáveis por **whitelist** (`re.sub` com dicionário); qualquer `[Variável]` desconhecida é rejeitada na validação do formato. Número gravado na matrícula e **nunca recalculado**.
- **Número do recebimento**: sequência `RECEBIMENTO` por ano.
- Todos usam `SequenciaNumeracao` com `select_for_update()` dentro de `transaction.atomic()` + constraint única como rede de segurança.

## 4. Transições permitidas (Matrícula)

```
PENDENTE ──ativar──▶ ATIVA ──transferir──▶ TRANSFERIDA   (cria nova matrícula ATIVA com matricula_anterior)
   │                   ├────concluir────▶ CONCLUIDA
   └──cancelar──▶ CANCELADA ◀──cancelar──┘
```
`TRANSFERIDA`, `CANCELADA` e `CONCLUIDA` são finais.

## 5. Fluxos de navegação

- **Aluno**: Alunos → Novo aluno → (salvar) → Ficha → *Vincular a turma* → tipo/ano/série/turma → plano e responsável → **prévia das parcelas** → confirmar → ficha (aba Financeiro).
- **Recebimento**: Caixa → pesquisar aluno → selecionar → marcar parcelas (valor por parcela) → forma de pagamento + valor recebido (troco calculado) → confirmar → comprovante imprimível.

## 6. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Concorrência em sequências no SQLite | Transação + retry em `IntegrityError`; teste com threads; em PostgreSQL `select_for_update` bloqueia de fato |
| Duplo clique / reenvio de recebimento | Token único + botão desabilitado no JS (apenas conveniência) |
| Troca de plano alterando parcelas antigas | Parcela guarda snapshots; plano com `PROTECT`; alteração de plano só afeta novas contratações |
| Troca de responsável alterando dívidas antigas | `Parcela.responsavel` é FK fixada na geração; troca cria novo registro de histórico |
| Estorno incoerente | Serviço recalcula `valor_pago`/`status` a partir das alocações não estornadas |
| Vazamento entre instituições | `InstituicaoQuerysetMixin` em todas as views; teste dedicado |
| Exposição de CPF | Listagens mostram CPF mascarado; completo somente na ficha para quem tem permissão |
