# Gestão Escolar — MVP (Secretaria e Financeiro)

Sistema web em **Django 5 + Bootstrap 5** para secretaria escolar (alunos, anos letivos, séries, turmas,
matrículas, numeração configurável) e financeiro (planos, parcelas, caixa, recebimentos, estornos,
relatórios), com perfis de acesso, auditoria e isolamento por instituição.

Documentação: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) (modelo de dados e decisões),
[`docs/REGRAS_DE_NEGOCIO.md`](docs/REGRAS_DE_NEGOCIO.md) (regras e limitações conhecidas),
[`docs/MANUAL_DO_CODIGO.md`](docs/MANUAL_DO_CODIGO.md) (manual do código: onde cada funcionalidade está e como o código é estruturado).

## Requisitos
Python 3.11+ (testado em 3.13) e **PostgreSQL 14+ instalado na máquina** (testado em 16). O SQLite continua
disponível apenas para desenvolvimento rápido e testes (basta não definir `DB_NAME`/`DATABASE_URL`).

## Visão geral (um sistema, várias escolas)
- **Administrador do sistema** (superusuário): entra por *"Sou administrador do sistema"* na tela de login,
  cadastra as escolas (com logotipo) e o administrador de cada uma. Não acessa dados de secretaria/financeiro.
- **Administrador da escola**: perfil *Administrador*, criado no cadastro da escola. Tem acesso total, **somente** à
  escola à qual está vinculado, e configura usuários, numeração, documentos etc.
- **Login**: o usuário escolhe a **escola**, depois informa **usuário e senha**. O mesmo usuário (ex.: `secretaria`)
  pode existir em escolas diferentes: são pessoas diferentes.
- **Isolamento**: todo dado de negócio pertence a uma escola (`instituicao_id` obrigatório no banco) e o sistema
  recusa gravar registros que relacionem escolas diferentes. Veja `docs/REGRAS_DE_NEGOCIO.md`.

## Instalação (com PostgreSQL)

> **Guia completo, passo a passo para Windows, com verificação e solução de problemas:**
> [`docs/INSTALACAO_POSTGRESQL.md`](docs/INSTALACAO_POSTGRESQL.md). Abaixo, o resumo.

1. **Instale o PostgreSQL** (https://www.postgresql.org/download/windows/). Anote a senha que você definir para o
   usuário `postgres` e mantenha a porta `5432`.
2. **Prepare o projeto** (Prompt de Comando, na pasta com `manage.py`):
   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy .env.example .env
   ```
3. **Edite o `.env`**: defina `DJANGO_SECRET_KEY` (gere com
   `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`) e invente
   `DB_PASSWORD` (uma senha **nova**, diferente da do `postgres`).
4. **Crie o banco, as tabelas e o administrador do sistema**:
   ```bat
   python manage.py criar_banco_postgres
   python manage.py migrate
   python manage.py createsuperuser
   python manage.py runserver
   ```
   `criar_banco_postgres` pede a senha do `postgres` e cria o usuário e o banco do `.env` (pode ser repetido).
5. **Primeiro acesso**: em `http://localhost:8000/` clique em **Sou administrador do sistema**, entre com o
   superusuário e use **Escolas → Nova escola** (nome, CNPJ, logotipo e administrador da escola). Depois saia,
   escolha a escola no login e entre com o administrador dela para configurar anos letivos, séries, turmas,
   numeração, usuários e documentos.

O logotipo da escola aparece no menu lateral e na tela de login (quando a escola é selecionada).
Algo deu errado? Veja a tabela de problemas no guia de instalação.

### Cobrança bancária (boleto com Pix)
Em *Configurações → Cobrança bancária* o administrador da escola informa o banco/provedor, gera um **boleto de teste**
e confirma o recebimento; só depois disso os boletos dos alunos passam a ser registrados no banco. Veja
`docs/REGRAS_DE_NEGOCIO.md` (seção "Cobrança bancária"). Defina no `.env` `BILLING_CRYPTO_KEY` e `DJANGO_SITE_URL`
e rode `pip install -r requirements.txt` e `python manage.py migrate`.

### Segurança HTTP em produção
O sistema já envia CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` e bloqueia TRACE/TRACK. Em
produção use **gunicorn atrás de Nginx com HTTPS** (nunca `runserver`), `DJANGO_DEBUG=False` e defina no `.env`
`DJANGO_PROXY_HTTPS`, `DJANGO_SSL_REDIRECT` e `DJANGO_HSTS_SEGUNDOS` (veja `.env.example`). No Nginx, esconda a versão
do servidor (`server_tokens off;`). Teste com `python manage.py check --deploy`.

### Assistente de IA (opcional)
O administrador do sistema habilita o módulo em *Escolas → editar escola → Habilitar o assistente de IA*. Depois o administrador
da escola abre *Configurações → Assistente de IA (integração)*, informa a **chave de API da própria escola** (Anthropic/Claude),
aceita os termos, testa a conexão e ativa. Para treinar sem custo, use a chave `DEMO`. O assistente só enxerga totais
(LGPD). Detalhes em `docs/REGRAS_DE_NEGOCIO.md`. Rode `python manage.py migrate`.

### Atualizando uma instalação existente (escola única)
Faça **backup do banco** e rode `python manage.py migrate`. As migrations preenchem a escola nos registros antigos
e o login de cada usuário. Usuários e superusuários que já pertenciam à escola continuam entrando **escolhendo essa
escola** na tela de login.

### Contas a pagar (nova funcionalidade)
Depois de atualizar os arquivos, rode `python manage.py migrate`: ele cria as tabelas, acrescenta duas opções em
**Configurações gerais** (permitir saldo negativo; dias de antecedência do aviso) e libera as permissões nos perfis
Administrador, Financeiro e Consulta. O acesso fica em **Financeiro → Contas a pagar**. Regras em
`docs/REGRAS_DE_NEGOCIO.md`.

### Outros bancos
`DATABASE_URL=postgres://usuario:senha@localhost:5432/escola` substitui as variáveis `DB_*`.
As sequências de numeração usam `select_for_update`, que bloqueia linhas de fato no PostgreSQL.

### Dados de demonstração (opcional)
```bash
python manage.py dados_demo --senha 'ESCOLHA-UMA-SENHA'
```
Cria uma instituição separada ("Escola Demonstração (dados fictícios)"), o usuário `demo` (Administrador) com a
senha informada, alunos, turmas, planos, matrículas e um recebimento. Não toca nos dados reais (isolamento por
instituição). Não há senha padrão.

### Outros comandos
```bash
python manage.py carregar_series     # recarrega a tabela de referência de séries (idempotente)
python manage.py test tests          # suíte de testes (no PostgreSQL, o usuário do banco precisa de CREATEDB)
```

## Estrutura
```
config/            settings, urls
apps/core          escolas (instituições), cadastro de escolas, configurações, numeração, dashboard, busca
apps/accounts      perfis, grupos/permissões, usuários
apps/students      alunos, responsáveis, histórico de responsável financeiro
apps/academic      anos letivos, séries (referência e ofertadas), turmas
apps/enrollments   matrículas/vínculos, transferência
apps/finance       planos, parcelas, caixa, recebimentos, estornos, relatórios
apps/payables      contas a pagar: liquidação, saldo, notificações de vencimento
apps/audit         trilha de auditoria
apps/documents     modelos de documentos, cabeçalho/logotipo e emissão (aluno/responsável)
templates/ static/ tests/ docs/
```
Regras críticas ficam em `services.py` de cada app (transações e validações), não em views/templates.

## Segurança e privacidade (resumo)
Permissões verificadas no servidor; CSRF ativo; CPF mascarado nas listagens (completo só com permissão);
auditoria sem senhas nem dados pessoais desnecessários; sem exclusão física de histórico acadêmico/financeiro.
Antes de usar com dados reais, avalie as obrigações da LGPD (dados de crianças e adolescentes) com
o responsável jurídico da instituição — o sistema oferece medidas técnicas básicas, não conformidade completa.
Em produção: `DJANGO_DEBUG=False`, `DJANGO_ALLOWED_HOSTS`, HTTPS e `DJANGO_SECURE_COOKIES=True`.
