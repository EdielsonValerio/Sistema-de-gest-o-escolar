# Gestão Escolar — MVP (Secretaria e Financeiro)

Sistema web em **Django 5 + Bootstrap 5** para secretaria escolar (alunos, anos letivos, séries, turmas,
matrículas, numeração configurável) e financeiro (planos, parcelas, caixa, recebimentos, estornos,
relatórios), com perfis de acesso, auditoria e isolamento por instituição.

Documentação: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) (modelo de dados e decisões),
[`docs/REGRAS_DE_NEGOCIO.md`](docs/REGRAS_DE_NEGOCIO.md) (regras e limitações conhecidas).

## Requisitos
Python 3.11+ (testado em 3.13). Banco: SQLite (padrão, sem configuração) ou PostgreSQL (recomendado em produção).

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                 # edite e defina DJANGO_SECRET_KEY
python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"   # use a saída no .env
python manage.py migrate             # cria tabelas, perfis de acesso e a tabela de séries
python manage.py createsuperuser     # primeiro administrador
python manage.py runserver
```

Acesse `http://localhost:8000/` e entre com o superusuário. No primeiro acesso, a instituição
("Minha Escola") e o perfil do usuário são criados automaticamente; renomeie em *Configurações*.
Crie os demais usuários em **Configurações → Usuários e permissões** e atribua um perfil:
**Administrador, Secretaria, Financeiro** ou **Consulta**.

### PostgreSQL
No `.env`: `DATABASE_URL=postgres://usuario:senha@localhost:5432/escola` (o driver `psycopg2-binary` já está no
`requirements.txt`). As sequências usam `select_for_update`, que bloqueia linhas de fato no PostgreSQL.

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
python manage.py test tests          # suíte de testes
```

## Estrutura
```
config/            settings, urls
apps/core          instituição, configurações, numeração, dashboard, busca
apps/accounts      perfis, grupos/permissões, usuários
apps/students      alunos, responsáveis, histórico de responsável financeiro
apps/academic      anos letivos, séries (referência e ofertadas), turmas
apps/enrollments   matrículas/vínculos, transferência
apps/finance       planos, parcelas, caixa, recebimentos, estornos, relatórios
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
"# Sistema-de-gest-o-escolar" 
