# Manual do código — Gestão Escolar

Manual de consulta para quem vai manter ou evoluir o sistema. Ele diz **onde cada funcionalidade está implementada**,
**como o código está organizado** e **quais regras não podem ser quebradas**.

Os documentos irmãos continuam valendo e não são repetidos aqui:

| Documento | Para que serve |
|---|---|
| `README.md` | Instalação resumida e visão geral |
| `docs/INSTALACAO_POSTGRESQL.md` | Passo a passo do banco de dados |
| `docs/ARQUITETURA.md` | Modelo de dados e decisões de projeto (premissas P1–P15) |
| `docs/REGRAS_DE_NEGOCIO.md` | Regras de negócio e limitações, por área |
| **este manual** | Mapa do código: arquivos, funções, fluxos e como estender |

> Dica de uso: comece pelo **capítulo 1** (visão geral) e pelo **capítulo 13** (índice "onde está…?"). Os capítulos 6 a 14
> são consulta por módulo.

---

## Sumário

1. [Visão geral e tecnologias](#1-visão-geral-e-tecnologias)
2. [Estrutura de pastas](#2-estrutura-de-pastas)
3. [Como o código é organizado (camadas e convenções)](#3-como-o-código-é-organizado-camadas-e-convenções)
4. [Configuração, banco e comandos](#4-configuração-banco-e-comandos)
5. [Várias escolas, login e permissões](#5-várias-escolas-login-e-permissões)
6. [App `core` — escola, configurações, numeração, painel](#6-app-core--escola-configurações-numeração-painel)
7. [App `accounts` — login, usuários e perfis](#7-app-accounts--login-usuários-e-perfis)
8. [App `audit` — auditoria](#8-app-audit--auditoria)
9. [App `students` — alunos e responsáveis](#9-app-students--alunos-e-responsáveis)
10. [App `academic` — anos letivos, séries e turmas](#10-app-academic--anos-letivos-séries-e-turmas)
11. [App `enrollments` — matrículas](#11-app-enrollments--matrículas)
12. [App `finance` — contas a receber, caixa e relatórios](#12-app-finance--contas-a-receber-caixa-e-relatórios)
13. [App `payables` — contas a pagar](#13-app-payables--contas-a-pagar)
14. [App `documents` — documentos e editor de leiaute](#14-app-documents--documentos-e-editor-de-leiaute)
    - [14b. App `billing` — cobrança bancária (boleto com Pix)](#14b-app-billing--cobrança-bancária-boleto-registrado-com-pix)
    - [14c. App `assistente` — assistente de IA (chat com Claude)](#14c-app-assistente--assistente-de-ia-chat-com-claude)
15. [Interface: templates, CSS e JavaScript](#15-interface-templates-css-e-javascript)
16. [Mapa de URLs](#16-mapa-de-urls)
17. [Auditoria: ações registradas](#17-auditoria-ações-registradas)
18. [Testes](#18-testes)
19. [Migrações](#19-migrações)
20. [Receita: como adicionar uma funcionalidade](#20-receita-como-adicionar-uma-funcionalidade)
21. [Índice "onde está…?"](#21-índice-onde-está)
22. [Armadilhas e limitações conhecidas](#22-armadilhas-e-limitações-conhecidas)

---

## 1. Visão geral e tecnologias

Sistema web **multi-escola** (um único sistema atende várias escolas, cada uma isolada das outras) para:

- **Secretaria:** alunos, responsáveis, anos letivos, séries, turmas, matrículas, numeração configurável.
- **Financeiro:** planos, contas a receber (parcelas), caixa, recebimentos, estornos, relatórios e **contas a pagar**.
- **Documentos:** modelos de documento com editor e variáveis, cabeçalho com logotipo, emissão para aluno/responsável.
- **Transversal:** perfis de acesso, auditoria, isolamento por escola.

| Item | Escolha |
|---|---|
| Linguagem / framework | Python 3.11+ (testado em 3.13), Django 5.x (`requirements.txt`: `Django>=5.1,<6`) |
| Banco | PostgreSQL 14+ (recomendado); SQLite apenas para desenvolvimento e testes |
| Interface | Templates do Django + Bootstrap 5.3.3 e Bootstrap Icons 1.11.3 (via CDN) |
| JavaScript | Puro (sem framework): máscaras, caixa, editor de documentos |
| Imagens | Pillow (foto do aluno, logotipo da escola e do cabeçalho) |
| Idioma / fuso | `pt-br`, `America/Fortaleza` |
| Dinheiro | `Decimal` com 2 casas (nunca `float`), arredondamento `ROUND_HALF_UP` |

Tamanho aproximado: ~9.400 linhas de Python (sem migrações) em 9 apps, 202 testes automatizados.

### Ideias que estruturam tudo

1. **Escola = fronteira.** Toda tabela de negócio tem `instituicao_id` obrigatório. Nada atravessa escolas (cap. 5).
2. **Regra de negócio fica em `services.py`**, dentro de `transaction.atomic()`. View e template só orquestram e exibem.
3. **Dinheiro é imutável e auditável.** Nada de dinheiro é apagado: recebimento é estornado, liquidação é estornada,
   parcela é cancelada. As chaves estrangeiras usam `on_delete=PROTECT`.
4. **O banco também se defende:** constraints (`CheckConstraint`, `UniqueConstraint` condicionais) repetem as regras
   críticas, para que um erro de programação não corrompa dados.
5. **Valores derivados não são guardados** quando podem ser calculados: parcela *vencida* e *saldo do caixa* são
   sempre calculados na hora.

---

## 2. Estrutura de pastas

```
escola/
├── manage.py                  Ponto de entrada do Django
├── requirements.txt           Dependências
├── .env.example               Modelo das variáveis de ambiente (copie para .env; nunca versione o .env)
├── config/                    Configuração do projeto
│   ├── settings.py            Configurações (lê o .env), apps, middleware, banco, segurança
│   ├── urls.py                URLs raiz: monta as URLs de cada app
│   └── wsgi.py / asgi.py      Servidores
├── apps/                      Um app Django por área do sistema
│   ├── core/                  Escola (Instituicao), configurações, numeração, painel, mixins, isolamento
│   ├── accounts/              Login por escola, usuários, grupos/permissões
│   ├── audit/                 Registro de auditoria e tela de consulta
│   ├── students/              Aluno, Responsavel, vínculo com responsável financeiro
│   ├── academic/              Ano letivo, série de referência, série ofertada, turma
│   ├── enrollments/           Matrícula (vínculo aluno–turma) e suas transições
│   ├── finance/               Planos, contratos, parcelas, recebimentos, estornos, relatórios
│   ├── payables/              Contas a pagar, liquidações, saldo, notificações
│   └── documents/             Modelos de documento, variáveis, sanitização, emissão
├── templates/                 HTML (um subdiretório por app + base.html, components/, generic/)
├── static/                    css/app.css e js/ (app.js, caixa.js, editor_documento.js)
├── tests/                     Testes automatizados (7 arquivos + helpers)
└── docs/                      Documentação
```

Cada app segue (quando aplicável) o mesmo molde de arquivos:

| Arquivo | Papel |
|---|---|
| `models.py` | Tabelas, constraints, permissões customizadas (`Meta.permissions`), propriedades simples |
| `services.py` | **Regras de negócio** e operações que gravam (transacionais) |
| `forms.py` | Validação de entrada e campos dos formulários |
| `views.py` | Telas (classes baseadas em `View`/`TemplateView`/`ListView`) |
| `urls.py` | Rotas do app (`app_name` define o namespace) |
| `selectors.py` | Consultas reutilizáveis (só `students`) |
| `templatetags/` | Filtros e tags de template (`core/ui.py`, `payables/payables_ui.py`) |
| `context_processors.py` | Variáveis disponíveis em todos os templates (`core`, `payables`) |
| `migrations/` | Histórico do banco |

---

## 3. Como o código é organizado (camadas e convenções)

### 3.1 O caminho de uma requisição

```
Navegador ──► config/urls.py ──► apps/<app>/urls.py ──► View (apps/<app>/views.py)
                                                          │  1. AcessoMixin: login + escola + permissão (403)
                                                          │  2. Form: valida a entrada
                                                          ▼
                                                   services.py  (regra de negócio, transação, travas, auditoria)
                                                          │
                                                          ▼
                                                   models.py ──► PostgreSQL (constraints também protegem)
                                          ◄── Template (templates/<app>/…) renderiza a resposta
```

Regras de ouro:

- **Nunca** coloque regra de negócio em template ou view. Se a regra grava dados, ela mora em `services.py`.
- Serviços **levantam `ValidationError`** (do Django) com mensagem em português; a view captura e mostra ao usuário.
- Toda escrita relevante chama `audit.services.registrar(...)` dentro da mesma transação.
- Toda consulta de tela filtra por `instituicao` (os mixins fazem isso automaticamente).

### 3.2 Mixins e classes-base (`apps/core/mixins.py`)

| Classe | O que faz | Onde é usada |
|---|---|---|
| `AcessoMixin` | Exige login, **escola ativa** e a permissão declarada em `permission_required`. Autenticado sem permissão recebe **403**. Expõe `self.instituicao`. | Todas as views de escola |
| `PaginacaoMixin` | Define `paginate_by` pelo campo `itens_por_pagina` de `ConfiguracaoGeral` da escola | Listas |
| `InstituicaoQuerysetMixin` | `AcessoMixin` + `get_queryset()` filtrado pela escola | Listas e detalhes |
| `InstituicaoFormMixin` / `FormKwargsInstituicaoMixin` | Formulário que recebe `instituicao` e a aplica ao salvar | Forms de modelo |
| `BootstrapFormMixin` | Aplica classes do Bootstrap aos campos | Todos os forms |
| `SecoesMixin` | Permite agrupar campos em seções (`secoes`) | Forms longos |
| `BaseForm` / `BaseModelForm` | Form/ModelForm com escola, Bootstrap e **filtro automático de querysets** de campos relacionados pela escola (`_filtrar_querysets_por_instituicao`) | Quase todos os forms |
| `ObjetoFormView` | Criar/editar um objeto com ModelForm: `get_form`, `apos_salvar` (gancho para auditoria), mensagem e redirecionamento. **Sem exclusão** (preserva histórico). | CRUDs simples (ano letivo, série, turma, plano, modelo de documento) |

### 3.3 Padrão de views com ações (confirmação)

Operações sensíveis (liquidar, desativar, estornar, cancelar, transferir) usam uma tela de confirmação com
justificativa. Exemplos: `enrollments.views.MatriculaAcaoView`, `payables.views._AcaoView`
(+ `templates/payables/conta_acao.html`), `finance.views.RecebimentoEstornarView`.

### 3.4 Convenções de nomes

- Models, campos e funções em **português** sem acento (`instituicao`, `vencimento`, `liquidar_conta`).
- Serviços recebem argumentos nomeados (`*`) e devolvem o objeto criado/alterado.
- Nomes de URL no formato `app:recurso_acao` (`payables:conta_liquidar`, `finance:caixa_aluno`).
- Permissões customizadas em `Meta.permissions` do model dono da regra.
- `INSTITUICAO_DE = "<campo_pai>"` em models filhos: a escola é herdada do registro pai (cap. 5.4).

### 3.5 Dinheiro e datas

- `finance.services.parse_valor(texto)` aceita `1.234,56` e `1234.56`, recusa mais de 2 casas e valores não finitos.
- `finance.services.q2(valor)` arredonda para centavos (`ROUND_HALF_UP`).
- Em templates, `{{ valor|brl }}` (filtro em `core/templatetags/ui.py`) formata como `R$ 1.234,56`.
- Hoje = `timezone.localdate()` (fuso America/Fortaleza). Funções que dependem de "hoje" aceitam `hoje=` para testes.

---

## 4. Configuração, banco e comandos

### 4.1 `config/settings.py`

| Assunto | Como funciona |
|---|---|
| Variáveis | Lidas do `.env` por `python-dotenv` (se existir). Ver `.env.example`. |
| `DJANGO_SECRET_KEY` | **Obrigatória**; sem ela o sistema não inicia (nunca fica no código). |
| `DJANGO_DEBUG` | Liga/desliga modo de desenvolvimento. Em produção, cookies seguros (`DJANGO_SECURE_COOKIES`, padrão ligado) e redirecionamento HTTPS opcional (`DJANGO_SSL_REDIRECT`). |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Listas separadas por vírgula. |
| Banco | Função `_database()`: usa `DATABASE_URL`; senão `DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT` (PostgreSQL, `CONN_MAX_AGE=60`); senão **SQLite** (`transaction_mode=IMMEDIATE`, evita deadlock em escritas concorrentes). |
| Apps | `INSTALLED_APPS` lista os 9 apps. |
| Middleware | Inclui `apps.core.middleware.EscolaMiddleware` (prende a sessão à escola). |
| Context processors | `apps.core.context_processors.instituicao` (variável `instituicao_atual`) e `apps.payables.context_processors.notificacoes_contas_pagar` (variável `notif_contas`). |
| Autenticação | `AUTHENTICATION_BACKENDS`: `EscolaBackend` (escola+login+senha) e `ModelBackend` (administrador do sistema). |
| Segurança | Cookies `HttpOnly`/`SameSite=Lax`, `X_FRAME_OPTIONS=DENY`, `nosniff`. Log em `WARNING` e **nenhum log de formulário/POST** (evita registrar dados pessoais). |
| Mídia | `MEDIA_URL=media/`, `MEDIA_ROOT=media/`; servida pelo Django só com `DEBUG=True` (em produção, pelo servidor web). |

### 4.2 Comandos de gerenciamento (`apps/core/management/commands/`)

| Comando | O que faz |
|---|---|
| `criar_banco_postgres` | Cria usuário e banco PostgreSQL configurados no `.env` (pede a senha do `postgres`; pode repetir sem problema) |
| `carregar_series` | Carrega as séries de referência padrão (chama `academic.services.carregar_series_padrao`) |
| `dados_demo` | Cria dados de demonstração **em uma escola própria**, sem tocar nos dados reais |

Fluxo normal: `criar_banco_postgres` → `migrate` → `createsuperuser` → `runserver`.
Executar testes: `python manage.py test tests` (cap. 18).

---

## 5. Várias escolas, login e permissões

Este é o capítulo mais importante para não quebrar o sistema.

### 5.1 Quem é quem

| Papel | Como é | O que acessa |
|---|---|---|
| **Administrador do sistema** | Superusuário **sem** `PerfilUsuario` | Somente a área *Sistema* (escolas e séries de referência). Não vê dados de secretaria/financeiro. |
| **Administrador da escola** | Usuário do grupo *Administrador*, vinculado a uma escola | Tudo da própria escola |
| **Secretaria / Financeiro / Consulta** | Usuários do grupo correspondente | Conforme a matriz de permissões (5.5) |

### 5.2 Login (escola → usuário → senha)

- **View:** `apps/accounts/views.py::LoginView`; **form:** `accounts/forms.py::LoginForm`; **template:** `templates/accounts/login.html`.
- A tela lista as escolas ativas; o usuário escolhe a escola e informa login e senha. O botão *Sou administrador do
  sistema* usa `?sistema=1` e autentica pelo `ModelBackend`.
- **Backend:** `apps/accounts/backends.py::EscolaBackend.authenticate(escola, login, password)` busca o
  `PerfilUsuario` por `(instituicao, login iexact)`. Quando o usuário não existe, executa `set_password` num usuário
  fictício para igualar o tempo de resposta (não revela se o login existe).
- O mesmo login (`secretaria`) pode existir em escolas diferentes. Por isso o `User.username` interno é
  prefixado com o id da escola (`<id>.<login>`) e o login "de verdade" fica em `PerfilUsuario.login`
  (único por escola, sem diferenciar maiúsculas — constraint `uniq_perfil_login_escola`).
- Depois do login, a escola fica na sessão (`SESSAO_ESCOLA = "escola_id"`; `0` = modo sistema).
  Redirecionamento seguro de `next` (`url_has_allowed_host_and_scheme`).

### 5.3 Sessão presa à escola

`apps/core/middleware.py::EscolaMiddleware` roda em toda requisição autenticada:
define `request.instituicao` e **encerra a sessão** se a escola foi inativada ou se a escola da sessão não é a do
perfil. `apps/core/tenancy.py` oferece `perfil_do_usuario`, `instituicao_do_usuario` (levanta `PermissionDenied` →
403 se não houver escola ou ela estiver inativa) e `login_do_usuario`.

### 5.4 Trava na gravação (`apps/core/tenant.py`)

Um sinal `pre_save` (conectado em `CoreConfig.ready`) chama `verificar_escola(instance)` para **todo model de negócio**:

1. Se o registro tem `INSTITUICAO_DE` (ex.: `Liquidacao.INSTITUICAO_DE = "conta"`) e ainda não tem escola, herda do pai.
2. Recusa registro sem escola.
3. Para cada chave estrangeira cujo alvo também tem escola, confere que é **a mesma escola**; se não, levanta
   `EscolaIncompativel` (um `SuspiciousOperation` → HTTP 400).

Pontos importantes:
- `APPS_DE_NEGOCIO` lista os apps protegidos. **Ao criar um app novo com dados de escola, inclua-o ali.**
- `GLOBAIS` lista as tabelas compartilhadas (`academic.seriereferencia`, `core.instituicao`).
- `bulk_create` e `update()` **não disparam** o sinal; nesses pontos chame `verificar_escola(obj)` explicitamente
  (exemplo: `finance.services.contratar_plano`).

### 5.5 Perfis e permissões (`apps/accounts/permissions.py`)

- Permissões padrão do Django (`view_/add_/change_/delete_`) + permissões **customizadas** declaradas em
  `Meta.permissions` dos models (lista completa na tabela abaixo).
- `permissoes_por_perfil()` monta a matriz; `sincronizar_perfis()` (ligada ao `post_migrate`) cria/atualiza os
  grupos de forma idempotente. **Depois de criar uma permissão nova, rode `migrate`** para ela chegar aos grupos.
- O grupo **Administrador** recebe tudo, **exceto** o que é do sistema (cadastro de `Instituicao` e alteração de
  `SerieReferencia`).

Permissões customizadas:

| App | Código | Significado |
|---|---|---|
| accounts | `gerenciar_usuarios` | Gerenciar usuários e permissões |
| audit | `ver_auditoria` | Consultar a auditoria |
| students | `ver_cpf_completo` | Ver CPF sem máscara |
| enrollments | `transferir_matricula`, `cancelar_matricula` | Transferir/cancelar |
| finance | `aplicar_desconto`, `alterar_vencimento`, `corrigir_valor`, `cancelar_parcela` | Ajustes de parcela |
| finance | `gerar_cobrancas`, `matricular_sem_cobranca` | Cobranças na matrícula |
| finance | `registrar_recebimento`, `estornar_recebimento`, `ver_relatorios` | Caixa e relatórios |
| documents | `emitir_documento` | Emitir documentos |
| payables | `liquidar_conta`, `desativar_conta`, `estornar_liquidacao` | Contas a pagar |

Resumo da matriz por perfil:

| Perfil | Secretaria (alunos, turmas, matrículas) | Financeiro a receber | Contas a pagar | Estornos | Config./usuários |
|---|---|---|---|---|---|
| Administrador | tudo | tudo | tudo (inclui estornar liquidação) | sim | sim |
| Secretaria | CRUD + transferir/cancelar | só consulta; pode gerar cobranças na matrícula | **nenhum** | não | numeração (consulta) |
| Financeiro | consulta | planos (CRUD), ajustes, caixa, relatórios | CRUD + liquidar + desativar (**não** estorna) | recebimento: não (só Administrador) | não |
| Consulta | somente leitura | somente leitura | somente leitura | não | não |

### 5.6 Gestão da escola pelo administrador do sistema

`apps/core/views_escolas.py` (`SistemaMixin` exige superusuário): `EscolaListView`, `EscolaCreateView`,
`EscolaUpdateView`, `AdministradorCreateView`, `AdministradorSenhaView`. Regras em `apps/core/services_escolas.py`:
`cadastrar_escola` (cria escola, configurações e o administrador numa transação), `criar_administrador`,
`redefinir_senha`. Formulários em `core/forms_escolas.py` (valida nome único, CNPJ, logotipo, login).

---

## 6. App `core` — escola, configurações, numeração, painel

**Arquivos:** `models.py`, `views.py`, `views_escolas.py`, `forms.py`, `forms_escolas.py`, `numbering.py`,
`mixins.py`, `tenant.py`, `tenancy.py`, `middleware.py`, `context_processors.py`, `services_escolas.py`,
`templatetags/ui.py`, `management/commands/`.

### 6.1 Modelos (`core/models.py`)

| Model | Campos principais | Observações |
|---|---|---|
| `Instituicao` | `nome`, `cnpj`, `logotipo`, `ativa`, `criada_em` | Nome único sem diferenciar maiúsculas (`uniq_instituicao_nome`). Logotipo salvo por `logotipo_escola_path`. É a "escola". |
| `ConfiguracaoGeral` | `instituicao` (1–1), `exigir_plano_na_matricula`, `itens_por_pagina`, **`permitir_saldo_negativo`** (padrão falso), **`dias_aviso_contas_pagar`** (padrão 5, 0–60) | Criada sob demanda (`get_or_create`). |
| `ConfiguracaoNumeracao` | `instituicao`, `tipo`, `formato`, `separador`, `tamanho_sequencial`, `escopo`, `ativa` | Única por `(instituicao, tipo)`. |
| `SequenciaNumeracao` | `instituicao`, `tipo`, `chave_escopo`, `ultimo_valor` | Contador; único por `(instituicao, tipo, chave_escopo)`. |

Tipos de numeração (`TipoNumeracao`): `CODIGO_ALUNO`, `MATRICULA_OFICIAL`, `INSCRICAO_NAO_OFICIAL`, `RECEBIMENTO`.
Escopos (`EscopoNumeracao`): `INSTITUICAO`, `ANO_LETIVO`, `SERIE`, `TURMA`.

### 6.2 Numeração (`core/numbering.py`)

- **Formato** é um texto com variáveis entre colchetes: `[AnoLetivo][NumeroSerie][NumeroTurma][Sequencial]`.
  Variáveis possíveis: `AnoLetivo`, `AnoCadastro`, `NumeroSerie`, `NumeroTurma`, `Sequencial`, `CodigoAluno`
  (`VARIAVEIS`). O que cada tipo aceita está em `VARIAVEIS_POR_TIPO`; os valores padrão em `FORMATOS_PADRAO`.
- `validar_formato`, `renderizar`, `prever` (pré-visualização), `chave_escopo(escopo, …)` (monta a chave do contador).
- **`proxima_sequencia(instituicao, tipo, chave)`** — incrementa com `select_for_update` + `F()`; cria o contador com
  retry em `IntegrityError`. É **seguro para concorrência**. Contas a pagar a reutilizam (`tipo="CONTA_PAGAR"`).
- Geradores: `gerar_codigo_aluno`, `gerar_numero_recebimento`, `gerar_numero_matricula` (devolve `None` quando a turma
  não exige número). `obter_configuracao` cria a configuração padrão se faltar.
- Telas: `NumeracaoListView`, `NumeracaoEditView` (audita `NUMERACAO_ALTERADA`), `NumeracaoPreviaView` (prévia ao vivo).
  Form: `ConfiguracaoNumeracaoForm`.

### 6.3 Painel e busca (`core/views.py`)

- `DashboardView` (`templates/dashboard/dashboard.html`): quadros de alunos/turmas/matrículas (por permissão),
  financeiro (a receber, vencido, recebimentos do dia) e o **alerta de contas a pagar**
  (`payables/_dashboard_alerta.html`). O administrador do sistema é redirecionado a `core:escola_lista`.
- `BuscaRapidaView`: busca de alunos pela barra superior (usa `students.selectors.filtrar_por_busca`).
- `ConfiguracoesGeraisView` (`/configuracoes/`): edita `ConfiguracaoGeral` com `ConfiguracaoGeralForm`
  (valida `itens_por_pagina` e `dias_aviso_contas_pagar`).

### 6.4 Filtros de template (`core/templatetags/ui.py`)

`brl`, `qs` (monta querystring preservando filtros), `badge_parcela`, `badge_matricula`, `field_class`, `login_de`.

---

## 7. App `accounts` — login, usuários e perfis

| Arquivo | Conteúdo |
|---|---|
| `models.py` | `PerfilUsuario`: `user` (1–1), `instituicao`, `login`; `save()` deriva o `login` do `username` interno quando ele vem vazio (compatibilidade). O prefixo `<id da escola>.` no `username` é aplicado em `accounts/views.py` e `core/services_escolas.py`. Permissão `gerenciar_usuarios`. |
| `backends.py` | `EscolaBackend` (cap. 5.2) |
| `permissions.py` | Matriz de perfis e `sincronizar_perfis` (cap. 5.5) |
| `forms.py` | `LoginForm`, `UsuarioForm`, `validar_login_da_escola`, `validar_senha_confirmada` (usa os validadores de senha do Django) |
| `views.py` | `LoginView`, `LogoutView`, `UsuarioListView`, `UsuarioCreateView`/`UsuarioUpdateView` (via `UsuarioFormView`) |
| `urls.py` | `/entrar/`, `/sair/`, `/usuarios/`, `/usuarios/novo/`, `/usuarios/<pk>/` |

Regras: o administrador da escola só vê/edita usuários **da própria escola**; senhas passam pelos validadores do
Django; criação/alteração é auditada (`USUARIO_CRIADO`, `SENHA_REDEFINIDA`).

---

## 8. App `audit` — auditoria

- **Model** `RegistroAuditoria`: `instituicao`, `usuario` (FK, `SET_NULL`), `usuario_nome` (texto, sobrevive à
  remoção do usuário), `acao`, `entidade`, `entidade_id`, `aluno_id` (indexado, para o histórico do aluno),
  `descricao`, `dados` (JSON), `criado_em`.
- **Serviço** `audit/services.py::registrar(*, instituicao, usuario, acao, entidade, entidade_id="", aluno_id=None,
  descricao="", dados=None)`. A função interna `_limpar` converte `Decimal`/datas para JSON.
  **Regra:** `dados` guarda apenas campos não sensíveis (ids, valores, status) — **nunca** CPF, senha ou endereço.
- **Tela:** `AuditoriaListView` em `/auditoria/` (permissão `audit.ver_auditoria`), com filtros.
- Lista de ações: cap. 17.

---

## 9. App `students` — alunos e responsáveis

### 9.1 Modelos (`students/models.py`)

| Model | Descrição |
|---|---|
| `Aluno` | Cadastro **permanente** (identificação, contato, endereço, filiação, `ativo`). `codigo` único por escola e imutável (gerado pela numeração). CPF único por escola quando preenchido. Propriedades: `nome_exibicao`, `idade`, `cpf_mascarado`, `responsavel_financeiro_atual`. Foto em `foto_aluno_path`. Permissão `ver_cpf_completo`. |
| `Responsavel` | Pessoa física/jurídica que paga (`documento` CPF/CNPJ, e-mail de cobrança, telefone, endereço, `eh_proprio_aluno`, `ativo`). |
| `AlunoResponsavelFinanceiro` | **Histórico** de responsáveis: `aluno`, `responsavel`, `parentesco`, `inicio`, `fim` (nulo = vigente). Constraint: **no máximo um vigente por aluno**. |
| `UF` | `TextChoices` com as unidades da federação |

### 9.2 Serviços (`students/services.py`)

| Função | O que faz |
|---|---|
| `cadastrar_aluno(aluno, usuario)` | Gera o código único e grava; audita `ALUNO_CADASTRADO` |
| `responsavel_proprio_aluno(aluno)` | Cria/obtém o `Responsavel` "o próprio aluno" copiando os dados (snapshot) |
| `definir_responsavel_financeiro(aluno, responsavel, parentesco, usuario)` | Encerra o vínculo vigente e cria o novo; audita `RESPONSAVEL_FINANCEIRO_DEFINIDO` |
| `aplicar_responsavel_financeiro(aluno, dados, usuario)` | Interpreta os dados do formulário (próprio aluno, existente ou novo) e chama as funções acima |

### 9.3 Utilitários e consultas

- `students/utils.py`: `so_digitos`, `cpf_valido`, `cnpj_valido` (dígitos verificadores), `formatar_cpf/cnpj/documento`,
  `mascarar_documento` (exibe parcialmente o CPF sem a permissão `ver_cpf_completo`).
- `students/selectors.py::filtrar_por_busca(qs, q)`: busca por código, nome, nome social, nome da mãe, número de
  matrícula, nome do responsável financeiro e CPF.

### 9.4 Telas (`students/views.py`, `urls.py`)

| Rota | View | Observação |
|---|---|---|
| `/alunos/` | `AlunoListView` | Lista com busca e filtros |
| `/alunos/novo/` | `AlunoCreateView` | Usa `AlunoForm` (validações de CPF, datas, responsável) |
| `/alunos/<pk>/` | `AlunoFichaView` | Ficha: matrículas, responsáveis, financeiro, documentos |
| `/alunos/<pk>/editar/` | `AlunoUpdateView` | Audita `ALUNO_ALTERADO` |
| `/responsaveis/` … | `ResponsavelListView`, `ResponsavelDetailView`, `ResponsavelFormView` (+ Create/Update) | Audita `RESPONSAVEL_ALTERADO` |

Templates: `templates/students/` (`aluno_lista`, `aluno_form`, `aluno_ficha`, `responsavel_lista`,
`responsavel_detalhe`, `_matriculas_tabela`).

---

## 10. App `academic` — anos letivos, séries e turmas

### 10.1 Modelos (`academic/models.py`)

| Model | Pontos importantes |
|---|---|
| `AnoLetivo` | `ano`, datas, `situacao` (planejamento/ativo/encerrado). Único por `(instituicao, ano)`; `data_fim >= data_inicio`. |
| `SerieReferencia` | **Tabela global** (compartilhada por todas as escolas): `codigo` interno estável, `nome`, `etapa` (EI, EF1, EF2, EM, OUT), `ordem` (usada em `[NumeroSerie]`). Gerida pelo administrador do sistema. |
| `SerieOfertada` | Série oferecida em um ano letivo (único `(ano_letivo, serie_referencia)`); herda a escola do ano. |
| `Turma` | `tipo` **OFICIAL** (ligada a ano e série) ou **NAO_OFICIAL** (curso livre); `situacao` (planejada/ativa/encerrada); `capacidade`; `exige_numero_matricula`; `codigo` único por escola. Constraints garantem coerência tipo × ano/série. |

### 10.2 Serviços e formulários

- `academic/services.py`: `gerar_codigo_turma(instituicao)`, `carregar_series_padrao()` (idempotente; usada pelo
  comando `carregar_series` e pelos testes).
- `academic/forms.py`: `AnoLetivoForm`, `SerieReferenciaForm`, `SerieOfertadaForm`, `TurmaForm`
  (valida capacidade e coerência de datas).

### 10.3 Telas (`/secretaria/…`)

Anos (`ano_lista|ano_novo|ano_editar`), séries ofertadas (`serie_*`), séries de referência (`serie_ref_*`, só
superusuário — `SerieReferenciaFormView.has_permission`), turmas (`turma_lista|turma_nova|turma_detalhe|turma_editar`).
A função `_audit` de `views.py` centraliza o registro de auditoria dos cadastros.

---

## 11. App `enrollments` — matrículas

### 11.1 Modelo (`enrollments/models.py`)

`Matricula`: `aluno`, `turma`, `tipo` (oficial/não oficial), `ano_letivo` (só oficial), `numero`, `status`,
`data_ingresso`, `data_saida`, `matricula_anterior` (cadeia de transferências), `observacoes`.

Estados e transições permitidas (`Matricula.TRANSICOES`):

```
PENDENTE ──► ATIVA ──► CONCLUIDA
   │           ├────► TRANSFERIDA
   └────────┬──┴────► CANCELADA        (TRANSFERIDA, CANCELADA e CONCLUIDA são finais)
```

`STATUS_VIGENTES = (PENDENTE, ATIVA)`. Constraints: uma matrícula **oficial vigente por aluno e ano**
(`uniq_matricula_oficial_vigente_por_ano`), um vínculo vigente por aluno e turma, número único por escola.
Permissões: `transferir_matricula`, `cancelar_matricula`.

### 11.2 Serviços (`enrollments/services.py`)

| Função | Regra |
|---|---|
| `criar_matricula(*, aluno, turma, usuario, …)` | Valida turma/aluno (`_validar_turma_e_aluno`) e capacidade (`_validar_capacidade`), impede duplicidade, gera o número, grava e audita `MATRICULA_CRIADA`. `IntegrityError` vira mensagem amigável. |
| `ativar_matricula` | PENDENTE → ATIVA |
| `concluir_matricula` | ATIVA → CONCLUIDA (com `data_saida`) |
| `cancelar_matricula(…, justificativa, cancelar_parcelas_em_aberto=False)` | Exige justificativa; pode cancelar as parcelas em aberto (chama `finance.cancelar_parcelas_da_matricula`) |
| `transferir_matricula(matricula, nova_turma, …, justificativa)` | Só oficial; destino oficial, do mesmo ano e diferente; marca a antiga como TRANSFERIDA e cria a nova (via `criar_matricula`) ligada por `matricula_anterior` |

Toda transição bloqueia a linha (`_bloquear` → `select_for_update`) e valida com `_exigir_transicao`.
A integração com cobrança está em `finance.services.matricular_com_cobranca` (matricula + plano numa transação).

### 11.3 Telas (`/matriculas/…`)

`MatriculaListView` (`lista`), `VincularTurmaView` (`aluno/<pk>/vincular/`: escolhe turma e, se configurado, o plano
financeiro com prévia de parcelas), `GerarCobrancasView` (`<pk>/cobrancas/`), `MatriculaAcaoView` (`<pk>/<acao>/`:
ativar, concluir, cancelar, transferir). Forms em `enrollments/forms.py` (`VinculoForm`, `CobrancaForm`,
`JustificativaForm`, `TransferenciaForm`, `CancelamentoForm`, `ConclusaoForm`).
Templates em `templates/enrollments/` (`lista`, `vincular`, `cobrancas`, `_plano`).

---

## 12. App `finance` — contas a receber, caixa e relatórios

### 12.1 Modelos (`finance/models.py`)

| Model | Descrição |
|---|---|
| `PlanoFinanceiro` | Modelo de cobrança de uma **turma**: `valor_total`, `quantidade_parcelas`, `data_primeira_parcela`, `dia_vencimento`, `desconto_padrao_percentual`, encargos (`aplicar_encargos`, `multa_percentual`, `juros_mensal_percentual`), `situacao` (ativo/inativo). `valor_parcela` é só referência (calculado). |
| `ContratoFinanceiro` | Plano contratado por **uma matrícula** (1–1). Guarda **cópia** dos valores do plano no momento (histórico não muda). |
| `Parcela` | Conta a receber. `valor_original`, `desconto`, `acrescimo`, `valor_devido`, `valor_pago`, `vencimento`, `quitacao`, `status` (ABERTA, PARCIAL, QUITADA, CANCELADA). Propriedades `saldo`, `vencida`, `situacao_exibicao`; `recalcular_valor_devido()`. `ParcelaQuerySet`: `pendentes()`, `vencidas()`, `a_vencer()`. Constraints: valores não negativos, desconto ≤ original, pago ≤ devido. |
| `AjusteParcela` | Histórico de ajustes (DESCONTO, VENCIMENTO, VALOR, ENCARGOS, CANCELAMENTO) com valor antes/depois e justificativa |
| `Recebimento` | Entrada de dinheiro: `numero`, `aluno`, `forma` (DINHEIRO, PIX, CREDITO, DEBITO…), `valor_total` (aplicado às parcelas), `valor_recebido`, `troco`, `token_idempotencia` (**único**), `operador`, `status` (CONFIRMADO/ESTORNADO). |
| `AlocacaoRecebimento` | Quanto de cada recebimento foi para cada parcela (`valor`, `estornada`) |
| `Estorno` | Registro do estorno (1–1 com o recebimento): justificativa, usuário |

### 12.2 Serviços (`finance/services.py`) — o coração das regras

**Cálculos**

| Função | O que faz |
|---|---|
| `q2`, `parse_valor` | Arredondamento e leitura segura de valores |
| `somar_meses(base, meses, dia)` | Data do mês seguinte limitada ao último dia do mês (31 → 28/30) |
| `calcular_cronograma(...)` / `cronograma_do_plano(plano)` | Divide o total em parcelas; **a diferença de centavos fica na última parcela**; aplica desconto padrão |
| `recalcular_status(parcela)` | Define ABERTA/PARCIAL/QUITADA conforme valor pago × devido (não mexe em CANCELADA) |
| `calcular_encargos(parcela, hoje)` | Multa (única) + juros simples proporcionais aos dias de atraso sobre `original − desconto`; zero se o contrato não aplica encargos |
| `acrescimo_atual` | Maior entre o gravado e o calculado (nunca diminui sozinho) |

**Contratação**

| Função | O que faz |
|---|---|
| `contratar_plano(*, matricula, plano, responsavel, usuario)` | Valida escola, plano ativo, turma igual, matrícula vigente, responsável ativo, **um contrato por matrícula**; cria o contrato e as parcelas em lote (`bulk_create` + `verificar_escola`); audita `PARCELAS_GERADAS` |
| `matricular_com_cobranca(...)` | Cria matrícula e contrato na mesma transação |

**Ajustes de parcela** (todos exigem justificativa, bloqueiam a linha, só em parcelas editáveis e gravam `AjusteParcela`):
`aplicar_desconto`, `alterar_vencimento`, `corrigir_valor`, `cancelar_parcela`, `cancelar_parcelas_da_matricula`.

**Recebimento**

`registrar_recebimento(*, aluno, itens, forma, valor_recebido, usuario, token, referencia="", hoje=None)`:

1. **Idempotente:** se o `token` já existe, devolve o recebimento existente (e `criado=False`). Repete a checagem
   depois de travar as parcelas (requisições simultâneas idênticas) e trata `IntegrityError`.
2. Trava as parcelas com `select_for_update` (em ordem de `pk`, evita deadlock) e confere que são do aluno e da escola.
3. Cada parcela deve estar ABERTA/PARCIAL; atualiza encargos; valor > 0 e ≤ saldo (não existe adiantamento).
4. Em dinheiro, `valor_recebido ≥ total`; a diferença é **troco** (nunca vira receita). Outras formas: sem troco.
5. Gera o número (`gerar_numero_recebimento`), grava recebimento e alocações, atualiza `valor_pago`/status/`quitacao`.
6. Audita `RECEBIMENTO_REGISTRADO`.

`estornar_recebimento(recebimento, usuario, justificativa)`: exige justificativa; recusa se já estornado; marca as
alocações como estornadas, **recalcula** o `valor_pago` de cada parcela pelas alocações restantes, reabre a parcela
(se não cancelada), grava `Estorno` e audita `RECEBIMENTO_ESTORNADO`. O recebimento **permanece** (status ESTORNADO).

### 12.3 Telas (`/financeiro/…`)

| Rota | View | Função |
|---|---|---|
| `planos/`, `planos/novo/`, `planos/<pk>/`, `planos/<pk>/editar/` | `PlanoListView`, `PlanoCreateView`, `PlanoDetailView`, `PlanoUpdateView` | Planos financeiros (`PlanoForm`) |
| `contas-a-receber/`, `…/<pk>/`, `…/<pk>/ajustar/` | `ParcelaListView`, `ParcelaDetailView`, `ParcelaAjustarView` | Lista com filtros (vencidas, a vencer…), detalhe e ajustes |
| `caixa/`, `caixa/aluno/<pk>/` | `CaixaBuscaView`, `CaixaAlunoView` | Busca o aluno e recebe parcelas (token de idempotência gerado ao abrir o formulário) |
| `recebimentos/<pk>/`, `…/estornar/` | `RecebimentoDetailView`, `RecebimentoEstornarView` | Recibo e estorno |
| `relatorios/recebimentos/`, `relatorios/estornos/` | `RelatorioRecebimentosView`, `RelatorioEstornosView` | Relatórios por período (`FiltroPeriodoForm`) |

Forms: `PlanoForm`, `AjusteParcelaForm`, `CaixaForm`, `FiltroPeriodoForm`.
Templates em `templates/finance/` (`caixa_busca`, `caixa_aluno`, `recibo`, `parcela_*`, `plano_*`, `relatorio_*`,
`_relatorio_tabs`). O JavaScript do caixa é `static/js/caixa.js` (cap. 15.3).

---

## 13. App `payables` — contas a pagar

Registra as **saídas financeiras** que saem do saldo formado pelos recebimentos.

### 13.1 Regras em uma página

- **Saldo** = soma de `Recebimento.valor_total` **confirmados** (não conta troco nem estornados) **−** soma das
  `Liquidacao.valor` **não estornadas**. É sempre calculado (`services.saldo`), nunca armazenado.
- A conta nasce **ABERTA** e pode ser **liquidada** (debita o saldo) ou **desativada** (exige motivo; sai das
  notificações e do saldo em aberto). Conta desativada pode ser **reativada**; liquidação pode ser **estornada**.
- **Saldo negativo:** controlado por `ConfiguracaoGeral.permitir_saldo_negativo` (padrão falso). Com a opção
  desligada, a **liquidação** que deixaria o saldo abaixo de zero é recusada com
  *"Não é permitido saldo negativo. Saldo disponível: R$ …; valor a liquidar: R$ …."*. A checagem é na liquidação
  (momento em que o dinheiro sai); cadastrar conta futura maior que o saldo é permitido e a tela avisa.
- **Notificações:** contas ABERTAS vencidas ou que vencem em até `dias_aviso_contas_pagar` dias.
- Só conta **ABERTA** pode ser editada. Cadastro tem **repetição mensal** (1 a 36 contas) e é **idempotente**.

### 13.2 Modelos (`payables/models.py`)

| Model | Campos e regras |
|---|---|
| `Categoria` (TextChoices) | Classificação da despesa |
| `FormaPagamento` (TextChoices) | Forma usada na liquidação |
| `ContaPagar` | `numero` (`CP-AAAA-00001`), `descricao`, `categoria`, `prestador`, `prestador_documento`, `numero_documento`, `valor`, `vencimento`, `observacoes`, `status` (ABERTA/LIQUIDADA/DESATIVADA), `grupo`/`parcela_numero`/`parcela_total` (contas repetidas), `token_criacao` (UUID único), `desativada_em/por`, `motivo_desativacao`, `criado_por/em`. Propriedades: `vencida`, `dias_para_vencer`, `prazo_texto`, `liquidacao_atual`. Constraints: valor > 0; desativada exige motivo. Permissões: `liquidar_conta`, `desativar_conta`, `estornar_liquidacao`. |
| `Liquidacao` | `conta`, `valor`, `data`, `forma`, `referencia`, `observacao`, `usuario`, `usuario_nome`, `estornada` + dados do estorno (`estornada_em/por`, `justificativa`). Constraints: valor > 0; **uma liquidação ativa por conta** (índice único parcial); estorno exige justificativa. `INSTITUICAO_DE = "conta"`. |

### 13.3 Serviços (`payables/services.py`)

| Função | O que faz |
|---|---|
| `configuracao(inst)` | `ConfiguracaoGeral` da escola (cria se faltar) |
| `saldo(inst)` | `{recebido, pago, saldo}` |
| `resumo(inst, hoje)` | Indicadores da tela: saldo, em aberto, vencido (valor e quantidade), a vencer, pago no mês |
| `contas_a_vencer(inst, hoje, dias)` | Base das notificações (da mais urgente à menos) |
| `criar_contas(*, instituicao, usuario, dados, repeticoes=1, token=None)` | Cria 1 a `MAX_REPETICOES` (36) contas mensais no mesmo `grupo` (`somar_meses`). **Idempotente** pelo `token_criacao` (`_ja_criadas`). Numera com `proxima_sequencia(…, "CONTA_PAGAR", "ANO:<ano>")`. Audita `CONTA_PAGAR_CRIADA`. |
| `editar_conta(conta, dados, usuario)` | Só ABERTA; audita `CONTA_PAGAR_ALTERADA` |
| `liquidar_conta(conta, *, usuario, data, forma, valor=None, referencia="", observacao="")` | Trava a linha de `ConfiguracaoGeral` **da escola** e a conta (duas liquidações simultâneas não gastam o mesmo saldo); recusa conta já liquidada/desativada, valor ≤ 0, data futura e saldo negativo (se não permitido); grava `Liquidacao`, marca LIQUIDADA e audita (com `saldo_apos` e `saldo_negativo`). |
| `desativar_conta(conta, usuario, motivo)` | Exige motivo; só ABERTA |
| `reativar_conta(conta, usuario)` | DESATIVADA → ABERTA |
| `estornar_liquidacao(conta, usuario, justificativa)` | Marca a liquidação como estornada e reabre a conta; exige justificativa |

### 13.4 Telas (`/financeiro/contas-a-pagar/…`)

> Em `config/urls.py` esta rota é montada **antes** de `/financeiro/` para não colidir com o app `finance`.

| Rota (nome) | View | Função |
|---|---|---|
| `""` (`conta_lista`) | `ContaListView` | Painel (saldo, em aberto, vencidas, a vencer) + filtros (`SITUACOES`, período, prestador…) |
| `nova/` (`conta_nova`) | `ContaCreateView` | Cadastro com repetição mensal e token de idempotência |
| `<pk>/` (`conta_detalhe`) | `ContaDetailView` | Dados, saldo, avisos, contas do mesmo grupo, histórico de liquidações |
| `<pk>/editar/` (`conta_editar`) | `ContaUpdateView` | Só ABERTA |
| `<pk>/liquidar/` (`conta_liquidar`) | `ContaLiquidarView` | Confirma data/forma/valor |
| `<pk>/desativar/` (`conta_desativar`) | `ContaDesativarView` | Pede o motivo |
| `<pk>/reativar/` (`conta_reativar`) | `ContaReativarView` | POST direto |
| `<pk>/estornar-liquidacao/` (`conta_estornar`) | `ContaEstornarLiquidacaoView` | Exige permissão `estornar_liquidacao` |

As ações compartilham `_AcaoView` (define `get_form` e `executar`; captura `ValidationError` e mostra a mensagem) e
o template genérico `templates/payables/conta_acao.html`.

### 13.5 Notificações (sino, menu e painel)

- **Context processor** `payables/context_processors.py::notificacoes_contas_pagar` publica `notif_contas`
  = `{total, itens (máx. 8), mais, vencidas}` apenas para quem tem `payables.view_contapagar` e escola.
- **Sino** no topo: `templates/payables/_sino.html` (incluído em `base.html`; id `sino-contas`, selo `.sino-badge`).
- **Selo no menu:** `templates/components/menu.html` (item "Contas a pagar").
- **Alerta no painel:** `templates/payables/_dashboard_alerta.html` (incluído em `dashboard.html`).
- Filtros de template: `payables/templatetags/payables_ui.py` (`badge_conta`, `classe_prazo`).

### 13.6 Limitação documentada

O **estorno de recebimento** (módulo `finance`, já existente) não é bloqueado quando faria o saldo ficar negativo.
Foi mantido assim para não alterar o que já estava implementado.

---

## 14. App `documents` — documentos e editor de leiaute

### 14.1 Modelos (`documents/models.py`)

| Model | Descrição |
|---|---|
| `CabecalhoDocumento` | Cabeçalho padrão da escola (1–1): logotipo e linhas de texto |
| `ModeloDocumento` | `nome`, `tipo` (um dos 61 tipos de `tipos.py`), `destinatario` (ALUNO/RESPONSAVEL), `conteudo` (HTML sanitizado com variáveis), `usar_cabecalho`, `ativo`. Nome único por escola. Permissão `emitir_documento`. |
| `DocumentoEmitido` | **Cópia** do HTML final emitido (`html`), com `aluno`/`responsavel`, `emitido_por`, `emitido_em`. O documento já emitido não muda se o modelo mudar depois. Propriedade `numero`. |

### 14.2 Arquivos de apoio

- `documents/tipos.py`: `TIPOS_DOCUMENTO` (lista dos 61 tipos), `CHOICES_TIPO`, `ROTULO_TIPO`.
- `documents/variaveis.py`:
  - `CATALOGO`: variáveis por grupo (Instituição, Aluno, responsável, matrícula…) com descrição e exemplo.
    Ex.: `[nomeAluno]`, `[matriculaUnica]`, `[cpfAluno]`, `[nomeEscola]`.
  - `montar_contexto(*, instituicao, usuario, aluno, responsavel)`: valores reais de cada variável.
  - `renderizar(html, valores)`: troca `[variavel]` por valores **escapados**; variável fora do catálogo é rejeitada
    (`variaveis_desconhecidas`) — nada é executado.
  - `sanitizar_html(html)`: `_Sanitizador` (baseado em `HTMLParser`) mantém só tags e estilos permitidos
    (`_TAGS`, alinhamento de texto) e **descarta** `script`, `style`, `iframe`, `object`, `embed`…; `texto_puro`.
- `documents/services.py`: `cabecalho_html`, `montar_html`, `previa_html`, `emitir_documento(*, modelo, usuario,
  aluno=None, responsavel=None)` (valida destinatário, modelo ativo e escola; grava e audita `DOCUMENTO_EMITIDO`).
- `documents/forms.py`: `ModeloDocumentoForm` (sanitiza o conteúdo e rejeita variáveis desconhecidas),
  `CabecalhoDocumentoForm` (valida o logotipo).

### 14.3 Telas

| Rota | View |
|---|---|
| `/configuracoes/documentos/` (lista, `novo/`, `<pk>/`) | `ModeloListView`, `ModeloCreateView`, `ModeloUpdateView` (`_ModeloFormBase`) |
| `/configuracoes/documentos/previa/` | `PreviaView` (POST, prévia do editor) |
| `/configuracoes/documentos/cabecalho/` | `CabecalhoView` |
| `/documentos/aluno/<pk>/`, `/documentos/responsavel/<pk>/` | `EmitirAlunoView`, `EmitirResponsavelView` (`_EmitirBase`) |
| `/documentos/emitidos/<pk>/` | `DocumentoView` (folha para impressão, `.doc-folha`) |

Templates: `templates/documents/` (`modelo_lista`, `modelo_form`, `emitir`, `documento`). O editor está em
`static/js/editor_documento.js` (cap. 15.3).

---

## 14b. App `billing` — cobrança bancária (boleto registrado com Pix)

Permite que **cada escola** use o seu banco/provedor: configura, gera um boleto de teste, confirma o recebimento e só
então os boletos dos alunos passam a ser registrados no banco. Regras de negócio em `docs/REGRAS_DE_NEGOCIO.md`
(seção "Cobrança bancária").

### Arquivos

| Arquivo | Papel |
|---|---|
| `models.py` | `ConfiguracaoBancaria` (1 por escola: provedor, ambiente, segredos **cifrados**, `webhook_slug`, situação CONFIGURANDO/ATIVA/SUSPENSA), `Cobranca` (boleto da parcela ou de TESTE), `EventoBanco` (avisos já recebidos), `TituloSimulado` (banco de mentira, tabela global) |
| `crypto.py` | Cofre Fernet: `cifrar`, `decifrar`, `mascarar`. Chave em `BILLING_CRYPTO_KEY` (ou derivada da SECRET_KEY) |
| `codigos.py` | Linha digitável ⇄ código de barras, SVG do código (ITF), BR Code Pix (`brcode`, `crc16`), QR em base64 |
| `providers/base.py` | Contrato `Provedor` (`verificar`, `emitir`, `consultar`, `cancelar`, `alterar`) e dados (`Titulo`, `Pagador`, `Emitido`, `Situacao`, `ErroProvedor`) |
| `providers/asaas.py` | Conector Asaas (HTTP por `urllib`; cabeçalhos `access_token` e `User-Agent`; sandbox/produção) |
| `providers/simulado.py` | Banco simulado para treinamento e testes (`Simulado.simular_pagamento`) |
| `providers/__init__.py` | `CONECTORES` e `provedor_da(config)`: **onde registrar um banco novo** |
| `services.py` | Regras: configuração, teste, ativação, registro, lote, baixa, cancelamento, sincronização, webhook, conciliação |
| `hooks.py` | Ganchos chamados pelo `finance` (depois do commit; nunca quebram a operação financeira) |
| `views.py`, `urls.py`, `forms.py` | Tela única de configuração, lista de cobranças, boleto imprimível, ações e **webhook** |
| `templatetags/billing_ui.py` | `badge_cobranca`, `linha_fmt`, `barras_do_boleto` |
| `management/commands/conciliar_cobrancas.py` | Consulta os boletos pendentes no banco (agendável) |
| `templates/billing/` | `configuracao.html` (4 etapas), `cobranca_lista.html`, `boleto.html`, `_parcela_boleto.html` |

### Principais funções (`services.py`)

`salvar_configuracao`, `testar_conexao`, `gerar_boleto_teste`, `teste_confirmado`, `ativar_cobranca`, `suspender_cobranca`,
`cobranca_ativa` (só devolve a configuração se ATIVA **e** com teste pago na configuração vigente),
`registrar_cobranca(parcela)` (idempotente; falha vira `Cobranca` em ERRO), `gerar_em_lote`, `consultar_e_baixar`,
`baixar_cobranca` (cria o `Recebimento` forma BOLETO via `finance.registrar_recebimento`, token = `Cobranca.token`),
`cancelar_cobranca`, `sincronizar_parcela`, `processar_webhook`, `conciliar`.

### Ligações com o módulo financeiro (únicas mudanças em `finance`)

- `Recebimento.Forma.BOLETO` e `Recebimento.formas_de_caixa()` (o caixa não oferece "Boleto"); migração `finance/0005`.
- `finance/services.py`: `contratar_plano` → `hooks.parcelas_geradas`; `_registrar_ajuste` → `hooks.parcela_alterada`
  (exceto encargos); `registrar_recebimento` → `hooks.recebimento_registrado`.
- `templates/finance/parcela_detalhe.html` inclui `billing/_parcela_boleto.html`.

### Rotas

| Rota | Função |
|---|---|
| `/configuracoes/cobranca/` (`billing:configuracao`) | Tela única (permissão `configurar_cobranca`); cada botão envia `acao=` (`salvar`, `conexao`, `teste`, `consultar`, `simular`, `ativar`, `suspender`, `lote`, `conciliar`, `novo_token`) tratada por `_acao_<nome>` |
| `/financeiro/cobrancas/` | Lista (sem testes), filtros e ações |
| `/financeiro/cobrancas/<pk>/boleto/` | Boleto para imprimir (barras + QR Pix) |
| `/financeiro/cobrancas/<pk>/consultar/`, `…/cancelar/`, `…/parcela/<pk>/gerar/` | Ações por POST |
| `/webhooks/cobranca/<uuid>/` | Aviso do banco (sem login; autentica por token no cabeçalho `asaas-access-token`) |

### Como adicionar um banco novo

1. Crie `providers/<banco>.py` com uma classe que herde de `Provedor` e implemente os 5 métodos.
2. Inclua o valor em `Provedor` (`models.py`) e o mapeamento em `CONECTORES` (`providers/__init__.py`).
3. Se o banco usa outro formato de aviso, crie a rota/validação do webhook correspondente em `views.py`.
4. Teste com respostas simuladas (veja `AsaasTests` em `tests/test_cobranca_bancaria.py`) e **valide no sandbox do banco**.

## 14c. App `assistente` — assistente de IA (chat com Claude)

**Para que serve.** Chat dentro do sistema (menu *Inteligência artificial → Assistente de IA*) em que gestor, secretaria e
financeiro pedem análises e gráficos (ex.: "alunos matriculados × valor recebido por mês"). É um módulo **opcional por escola**.

**Liberação em duas etapas.**
1. O **administrador do sistema** marca *Habilitar o assistente de IA* no cadastro/edição da escola (`EscolaForm.ia_habilitada` →
   `servicos.habilitar_pelo_sistema`; auditoria `IA_HABILITADA`/`IA_DESABILITADA`). Sem isso a escola não vê nada (403).
2. O **administrador da escola** abre *Configurações → Assistente de IA (integração)*: informa **a chave de API da própria escola**
   (cifrada com `apps.billing.crypto`), escolhe o modelo, define o limite mensal de tokens, aceita os termos, **testa a conexão** e
   **ativa**. Chave nova desativa e exige novo teste. A chave `DEMO` ativa o modo demonstração (sem rede, sem custo) para treinamento.

**Arquivos.**
| Arquivo | Função |
|---|---|
| `models.py` | `ConfiguracaoIA` (1–1 com a escola), `Conversa`, `Mensagem`, `UsoMensal` |
| `cliente.py` | `ClienteClaude` (API Messages por `urllib`, método `_post` mockável, erros amigáveis sem vazar a chave) e `ClienteDemo` |
| `ferramentas.py` | Ferramentas **somente leitura e agregadas**, filtradas pelas permissões do usuário; `validar_grafico` |
| `lgpd.py` | `redigir` (remove CPF/CNPJ/e-mail/telefone da pergunta) e `agrupar_pequenos` (grupos < 3 viram "Outros") |
| `servicos.py` | Configuração, ativação, limite mensal e o laço do agente (`responder`, máx. 6 passos) |
| `views.py` / `urls.py` | `ConfiguracaoView`, `ChatView`, `PerguntarView` (JSON), `ExcluirConversaView` |
| `context_processors.py` | Mostra o menu só se a escola está habilitada e o usuário tem permissão |

**Como funciona uma pergunta.** `PerguntarView` → `servicos.responder`: confere permissão e limite, remove dados pessoais, monta o
histórico (últimas 12 mensagens de texto), chama o modelo com as ferramentas que o usuário pode usar; quando o modelo pede uma
ferramenta, o **Django** a executa com a escola do usuário logado (nunca vinda do modelo) e devolve só números; o gráfico sai da
ferramenta `emitir_grafico`, validada por nós e desenhada no navegador com Chart.js. O modelo **nunca acessa o banco**.

**Ferramentas (permissões exigidas).** `matriculas_por_turma` (`enrollments.view_matricula`), `recebimentos_por_mes` e
`evolucao_matriculas_e_recebimentos` (`finance.ver_relatorios` [+ matrícula]), `inadimplencia_por_turma` e `a_receber_por_mes`
(`finance.view_parcela`), `contas_a_pagar_resumo` (`payables.view_contapagar`). Para criar outra: função `(escola, params) -> dict`
em `ferramentas.py`, entrada em `FERRAMENTAS` com `perms` e teste de que não vaza nome/CPF.

**Permissões.** `assistente.usar_assistente` (Administrador, Secretaria, Financeiro) e `assistente.configurar_ia` (Administrador).

**LGPD (resumo).** Só agregados; sem nomes, CPF, endereço, telefone, e-mail, responsáveis ou fornecedores; grupos < 3 pessoas
agrupados; dados pessoais digitados são removidos antes do envio; auditoria `IA_PERGUNTA` guarda ferramentas e tokens, **não o texto**;
termo de uso aceito (usuário e data); fase 1 é somente leitura (não altera nada no sistema).

---

## 15. Interface: templates, CSS e JavaScript

### 15.1 Templates (`templates/`)

| Pasta/arquivo | Papel |
|---|---|
| `base.html` | Estrutura: barra lateral (logotipo e nome da escola), barra superior (busca, **sino**, usuário), mensagens, blocos `title`/`content`. Carrega Bootstrap por CDN e `css/app.css?v=4`. |
| `components/menu.html` | **Menu lateral**; cada item é condicionado por `perms.<app>.<permissão>` (só exibição; a segurança real é nas views). |
| `components/` (`field`, `form_fields`, `messages`, `pagination`, `vazio`) | Peças reutilizáveis |
| `generic/` (`form`, `lista`, `confirm`) | Telas padrão usadas por `ObjetoFormView` e confirmações |
| `<app>/` | Telas de cada app; arquivos com prefixo `_` são parciais (`_sino.html`, `_plano.html`) |
| `dashboard/dashboard.html` | Painel inicial |

Para **adicionar um item de menu**, edite `components/menu.html` (e proteja com `perms`).
Ao alterar o CSS, aumente o `?v=` em `base.html` para forçar o navegador a recarregar.

### 15.2 CSS (`static/css/app.css`)

Variáveis de cor (`--brand`, `--brand-2`, `--bg`), menu lateral (`.sidebar`, com `!important` no fundo porque o
Bootstrap zera o `background` do `offcanvas-lg` acima de 992px), cartões e indicadores (`.kpi`), tabelas,
impressão (`@media print` esconde menu e alertas), folha de documento (`.doc-*`), logotipos (`.sidebar-logo`,
`.login-logo`) e sino de contas a pagar (`.sino-badge`, `.sino-menu`).

### 15.3 JavaScript (`static/js/`)

| Arquivo | Função |
|---|---|
| `app.js` | Máscaras (`data-mask`: cpf, cnpj, cpfcnpj, cep, tel) e proteção contra duplo clique (`form[data-once]`). **A validação real é sempre do servidor.** |
| `caixa.js` | Tela de caixa: totais, troco e validação visual **em centavos inteiros** (sem `float`). O servidor refaz todas as contas. |
| `editor_documento.js` | Editor de leiaute (HTML restrito), inserção de variáveis e limpeza no cliente. Sanitização real: `documents.variaveis.sanitizar_html`. |

---

## 16. Mapa de URLs

Montagem em `config/urls.py` (a ordem importa):

```
admin/                              Admin do Django
(raiz)  accounts   → /entrar/  /sair/  /usuarios/…
(raiz)  core       → /  /busca/  /configuracoes/  /configuracoes/numeracao/…  /sistema/escolas/…
(raiz)  students   → /alunos/…  /responsaveis/…
secretaria/         academic     → anos/  series/  series/referencia/  turmas/
matriculas/         enrollments  → ""  aluno/<pk>/vincular/  <pk>/cobrancas/  <pk>/<acao>/
financeiro/contas-a-pagar/  payables   (declarada ANTES de financeiro/)
financeiro/         finance      → planos/  contas-a-receber/  caixa/  recebimentos/  relatorios/
auditoria/          audit
(raiz)  documents  → /configuracoes/documentos/…  /documentos/…
media/ (só com DEBUG)
```

Use sempre `{% url 'app:nome' %}` / `reverse('app:nome')`; os nomes estão nas tabelas dos capítulos 9 a 14.

---

## 17. Auditoria: ações registradas

| Área | Ações (`acao`) |
|---|---|
| Escolas e usuários | `ESCOLA_CADASTRADA`, `ESCOLA_ALTERADA`, `ADMIN_ESCOLA_CRIADO`, `USUARIO_CRIADO`, `SENHA_REDEFINIDA` |
| Configuração | `NUMERACAO_ALTERADA`, `MODELO_DOCUMENTO_CRIADO`, `CABECALHO_DOCUMENTO_ALTERADO` |
| Alunos | `ALUNO_CADASTRADO`, `ALUNO_ALTERADO`, `RESPONSAVEL_ALTERADO`, `RESPONSAVEL_FINANCEIRO_DEFINIDO` |
| Matrículas | `MATRICULA_CRIADA`, `MATRICULA_ATIVADA`, `MATRICULA_CONCLUIDA`, `MATRICULA_CANCELADA`, `MATRICULA_TRANSFERIDA` (função `_audit` em `enrollments/services.py`) |
| Receber | `PLANO_CRIADO`, `PARCELAS_GERADAS`, `RECEBIMENTO_REGISTRADO`, `RECEBIMENTO_ESTORNADO` (e os ajustes de parcela) |
| Documentos | `DOCUMENTO_EMITIDO` |
| Cobrança bancária | `COBRANCA_CONFIG_ALTERADA`, `COBRANCA_WEBHOOK_TOKEN_RENOVADO`, `COBRANCA_TESTE_GERADA`, `COBRANCA_TESTE_CONFIRMADA`, `COBRANCA_ATIVADA`, `COBRANCA_SUSPENSA`, `COBRANCA_REGISTRADA`, `COBRANCA_ERRO`, `COBRANCA_ALTERADA`, `COBRANCA_CANCELADA`, `COBRANCA_PAGA`, `COBRANCA_DIVERGENCIA`, `COBRANCA_LOTE` |
| Assistente de IA | `IA_HABILITADA`, `IA_DESABILITADA`, `IA_CONFIGURADA`, `IA_ATIVADA`, `IA_DESATIVADA`, `IA_PERGUNTA` |
| Contas a pagar | `CONTA_PAGAR_CRIADA`, `CONTA_PAGAR_ALTERADA`, `CONTA_PAGAR_LIQUIDADA`, `CONTA_PAGAR_DESATIVADA`, `CONTA_PAGAR_REATIVADA`, `CONTA_PAGAR_LIQUIDACAO_ESTORNADA` |

Para uma nova ação, use o mesmo padrão `ENTIDADE_ACAO` em maiúsculas.

---

## 18. Testes

Rodar tudo: `python manage.py test tests`. Os testes de cobrança usam hash de senha rápido (`override_settings`) para não demorarem. Em PostgreSQL, o usuário do banco precisa poder criar o banco de teste
(`test_<nome>`); se uma execução anterior travou, remova o banco de teste antigo e rode de novo.

| Arquivo | Testes | Cobre |
|---|---|---|
| `test_fundacao.py` | 7 | Perfis/permissões, séries padrão, paginação e ordenação |
| `test_secretaria.py` | 21 | Alunos, acadêmico (anos, séries, turmas), formatos de numeração |
| `test_matriculas.py` | 19 | Matrículas, transições e **concorrência** |
| `test_financeiro.py` | 48 | Parcelas, ajustes, recebimento, estorno, vencimento e encargos |
| `test_documentos.py` | 22 | Variáveis, sanitização, emissão e telas |
| `test_multiescola.py` | 38 | Login por escola, área do sistema, usuários, **isolamento de dados** |
| `test_contas_pagar.py` | 47 | Saldo, liquidação, situação, cadastro, notificações, telas, isolamento, **concorrência** |
| `test_cobranca_bancaria.py` | 76 | Cofre, códigos de boleto/Pix, teste e ativação, registro de boletos, baixa, sincronização, conector Asaas (simulado), webhook, telas e permissões, isolamento |
| `test_assistente.py` | 40 | LGPD (remoção e agrupamento), ferramentas agregadas, permissões, isolamento, cliente Claude (simulado), laço do agente, limite mensal, telas, habilitação pela escola |
| **Total** | **278** | |

- `tests/helpers.py` oferece fábricas (`criar_instituicao`, `criar_usuario`, `criar_aluno`, `criar_turma_oficial`,
  `criar_plano`…). Use-as em testes novos.
- Testes de concorrência usam `TransactionTestCase` com threads (precisam de banco real; no SQLite as escritas são
  serializadas).
- Última execução registrada no desenvolvimento do módulo de contas a pagar: **202 testes OK em SQLite e em
  PostgreSQL**.

---

## 19. Migrações

Cada app tem `migrations/`. Pontos de atenção:

- A conversão para várias escolas usou o padrão em 3 passos: **adicionar a coluna anulável → preencher
  (`000x_preencher_instituicao`) → tornar obrigatória** (academic, accounts, finance, students). Mantenha esse
  padrão se for preciso incluir `instituicao` em tabelas já com dados.
- `core/0003_…` adiciona `permitir_saldo_negativo` e `dias_aviso_contas_pagar`; `payables/0001_initial` cria as
  tabelas de contas a pagar.
- Depois de qualquer mudança de permissões, rode `migrate` (o `post_migrate` sincroniza os grupos).
- **Backup do banco antes de `migrate` em produção.**

---

## 20. Receita: como adicionar uma funcionalidade

Exemplo: um novo cadastro "Fornecedor" dentro de um app existente.

1. **Model** em `models.py`: inclua `instituicao` (FK `PROTECT`), constraints de integridade, `Meta.permissions` se
   houver ações especiais. Em modelos filhos, defina `INSTITUICAO_DE`.
2. **App novo?** Inclua-o em `INSTALLED_APPS`, em `APPS_DE_NEGOCIO` (`core/tenant.py`) e em `APPS`
   (`accounts/permissions.py`).
3. `python manage.py makemigrations` e revise o arquivo gerado.
4. **Regra de negócio** em `services.py`: `@transaction.atomic`, `select_for_update` quando houver concorrência,
   `ValidationError` com mensagem em português, `registrar(...)` de auditoria.
5. **Form** herdando de `BaseModelForm`/`BaseForm` (já filtra relacionados pela escola e aplica Bootstrap).
6. **View** com `AcessoMixin` (`permission_required = "app.permissao"`); listas com `InstituicaoQuerysetMixin` +
   `PaginacaoMixin`; CRUD simples com `ObjetoFormView`.
7. **URL** em `urls.py` do app (e `config/urls.py` se for app novo — atenção à ordem de prefixos).
8. **Template** em `templates/<app>/`; item de menu em `components/menu.html` protegido por `perms`.
9. **Permissões por perfil:** edite `permissoes_por_perfil()` e rode `migrate`.
10. **Testes** em `tests/`: caminho feliz, validações, **permissão negada (403)**, **isolamento entre escolas** e, se
    houver dinheiro/sequência, **concorrência**.
11. **Documentação:** acrescente a seção em `docs/REGRAS_DE_NEGOCIO.md` e atualize este manual.

Checklist de segurança antes de entregar: (a) toda consulta filtra por escola; (b) nenhum dado sensível na
auditoria; (c) nenhum segredo no código; (d) valores em `Decimal`; (e) a view verifica permissão no servidor, não
só no menu.

---

## 21. Índice "onde está…?"

| Quero achar… | Onde |
|---|---|
| Como o login por escola funciona | `accounts/backends.py`, `accounts/views.py::LoginView`, `templates/accounts/login.html` |
| Por que um usuário não vê dados de outra escola | `core/tenant.py`, `core/middleware.py`, `core/mixins.py` (`InstituicaoQuerysetMixin`) |
| Matriz de permissões por perfil | `accounts/permissions.py::permissoes_por_perfil` |
| Cadastro de escolas e administradores | `core/views_escolas.py`, `core/services_escolas.py`, `core/forms_escolas.py` |
| Logotipo da escola no menu/login | `core/models.py::Instituicao.logotipo`, `templates/base.html`, `templates/accounts/login.html` |
| Formato do código do aluno / matrícula / recebimento | `core/numbering.py`, telas em `core/views.py` (Numeração) |
| Configuração "saldo negativo" e "dias de aviso" | `core/models.py::ConfiguracaoGeral`, `core/forms.py::ConfiguracaoGeralForm`, tela `/configuracoes/` |
| Cálculo de parcelas (centavos na última) | `finance/services.py::calcular_cronograma` |
| Multa e juros por atraso | `finance/services.py::calcular_encargos` |
| Situação "vencida" | `finance/models.py::ParcelaQuerySet`, `Parcela.vencida` |
| Receber pagamento (caixa) | `finance/services.py::registrar_recebimento`, `finance/views.py::CaixaAlunoView`, `static/js/caixa.js` |
| Proteção contra recebimento duplicado | `Recebimento.token_idempotencia` + `registrar_recebimento` |
| Estorno de recebimento | `finance/services.py::estornar_recebimento` |
| Transferência/cancelamento de matrícula | `enrollments/services.py` |
| Transições de matrícula | `enrollments/models.py::Matricula.TRANSICOES` |
| Responsável financeiro (histórico) | `students/services.py`, `students/models.py::AlunoResponsavelFinanceiro` |
| Máscara de CPF / permissão de ver CPF | `students/utils.py::mascarar_documento`, permissão `ver_cpf_completo` |
| Busca de alunos | `students/selectors.py::filtrar_por_busca` |
| Saldo do caixa | `payables/services.py::saldo` |
| Bloqueio de saldo negativo | `payables/services.py::liquidar_conta` |
| Notificação de contas a vencer | `payables/services.py::contas_a_vencer`, `payables/context_processors.py`, `templates/payables/_sino.html` |
| Repetição mensal de contas a pagar | `payables/services.py::criar_contas` |
| Variáveis dos documentos | `documents/variaveis.py::CATALOGO` |
| Segurança do HTML do editor | `documents/variaveis.py::sanitizar_html`, `static/js/editor_documento.js` |
| Emissão de documento | `documents/services.py::emitir_documento` |
| Registrar auditoria | `audit/services.py::registrar` |
| Painel inicial | `core/views.py::DashboardView`, `templates/dashboard/dashboard.html` |
| Menu lateral | `templates/components/menu.html` |
| Configuração do banco | `config/settings.py::_database`, `.env.example`, `docs/INSTALACAO_POSTGRESQL.md` |

---

## 22. Armadilhas e limitações conhecidas

- **`bulk_create` e `QuerySet.update()` não disparam o `pre_save`** de isolamento por escola. Se usar, valide
  com `verificar_escola`.
- **Esquecer o `?v=` do CSS** em `base.html` faz o navegador manter a versão antiga do estilo.
- **Menu usa `perms` só para exibir.** Se criar uma tela nova, a permissão também precisa estar na view
  (`permission_required`), senão a URL fica acessível digitando o endereço.
- **`/financeiro/contas-a-pagar/` deve vir antes de `/financeiro/`** em `config/urls.py`.
- **SQLite não tem travas de linha reais** (`select_for_update` é efetivo no PostgreSQL). Em produção use PostgreSQL.
- **Estorno de recebimento pode deixar o saldo do caixa negativo** (cap. 13.6).
- **Sem exclusão física** de alunos, matrículas, parcelas, recebimentos ou contas: use inativar, cancelar, desativar ou
  estornar.
- **Excedente em dinheiro é troco**, não adiantamento: não existe crédito antecipado no sistema.
- **Duas matrículas oficiais no mesmo ano** são proibidas por constraint, sem exceção configurável.
- **Mídia em produção:** o Django só serve `media/` com `DEBUG=True`; em produção configure o servidor web.
- **Segredos:** `DJANGO_SECRET_KEY` e senhas do banco ficam apenas no `.env` (que não deve ser versionado).
