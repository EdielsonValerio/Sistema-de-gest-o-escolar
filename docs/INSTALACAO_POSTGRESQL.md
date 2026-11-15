# Guia de instalação do banco de dados (PostgreSQL)

Este guia leva você do zero até o sistema funcionando com o PostgreSQL. Foi escrito para Windows; no fim há um
resumo para Linux. Tempo estimado: 15 minutos.

## Visão geral (o que vai acontecer)

| Etapa | O que você faz | Quem faz o quê |
|---|---|---|
| 1 | Instalar o PostgreSQL e guardar a senha do usuário `postgres` | Você, uma única vez, com o instalador |
| 2 | Conferir se o PostgreSQL está em execução | Você |
| 3 | Preparar o projeto e o arquivo `.env` | Você |
| 4 | Criar o banco e o usuário do sistema | O comando `criar_banco_postgres` |
| 5 | Criar as tabelas e o administrador do sistema | Os comandos `migrate` e `createsuperuser` |
| 6 | Iniciar o sistema e cadastrar a primeira escola | Você, no navegador |

Entenda os dois usuários, para não confundir:
- **`postgres`**: o administrador do PostgreSQL. Você define a senha dele ao instalar. Só serve para **criar** o banco.
- **`escola_app`** (nome sugerido): o usuário que **o sistema** usa no dia a dia. O comando da etapa 4 o cria
  com a senha que você escrever em `DB_PASSWORD` no `.env`.

---

## Etapa 1 — Instalar o PostgreSQL

1. Baixe o instalador para Windows em https://www.postgresql.org/download/windows/ (botão
   *Download the installer*, da EnterpriseDB). Escolha a versão 16 (ou mais recente), 64 bits.
2. Execute o instalador como administrador e avance até a tela **Select Components**. Deixe marcados:
   - **PostgreSQL Server** (obrigatório);
   - **pgAdmin 4** (programa visual para consultar o banco; recomendado);
   - **Command Line Tools** (comandos `psql` e `pg_dump`; recomendado).
   *Stack Builder* pode ficar desmarcado.
3. **Data Directory**: mantenha o padrão.
4. **Password**: digite a senha do usuário `postgres`. **Anote-a em local seguro**: o PostgreSQL não permite
   recuperá-la depois (só redefini-la com procedimento técnico).
5. **Port**: mantenha **5432**. **Locale**: mantenha o padrão.
6. Conclua a instalação. Se o instalador oferecer o Stack Builder no final, pode fechar.

> Já tem outro PostgreSQL instalado nesta máquina? Ele pode estar usando a porta 5432. Nesse caso o novo escolhe
> outra (por exemplo 5433): anote-a e use-a em `DB_PORT` no `.env`.

## Etapa 2 — Conferir se o PostgreSQL está em execução

1. Pressione **Windows + R**, digite `services.msc` e Enter.
2. Procure **postgresql-x64-16** (o número é a versão). A coluna *Status* deve mostrar **Em execução** e o
   *Tipo de inicialização* **Automático**. Se não estiver em execução, clique com o botão direito → **Iniciar**.

Teste opcional de senha: no menu Iniciar abra **SQL Shell (psql)**, pressione Enter nas quatro primeiras perguntas
(servidor, banco, porta, usuário) e digite a senha do `postgres`. Se aparecer `postgres=#`, está tudo certo.
Digite `\q` para sair.

## Etapa 3 — Preparar o projeto

Abra o **Prompt de Comando** na pasta do projeto (a que contém o arquivo `manage.py`) e execute:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Depois abra o arquivo `.env` com o Bloco de Notas e preencha:

```ini
DJANGO_DEBUG=True
DJANGO_SECRET_KEY=cole-aqui-a-chave-gerada

DB_NAME=escola
DB_USER=escola_app
DB_PASSWORD=escolha-uma-senha-forte
DB_HOST=localhost
DB_PORT=5432
```

- **`DJANGO_SECRET_KEY`**: gere com o comando abaixo e cole a linha inteira que ele imprimir.
  ```bat
  python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
  ```
- **`DB_NAME`**: nome do banco que será criado (`escola` está bom).
- **`DB_USER`**: usuário que o sistema usará (`escola_app` está bom). **Não use `postgres`.**
- **`DB_PASSWORD`**: invente uma senha forte **nova**. **Não** é a senha do `postgres`.
  Se ela tiver espaços ou o caractere `#`, coloque-a entre aspas: `DB_PASSWORD="minha #senha"`.
- Não deixe espaços ao redor do `=` e não coloque aspas nos demais valores.
- **`DATABASE_URL`** deve ficar vazio (ou ser removido): ele só é usado como alternativa às linhas `DB_*`.
- O arquivo `.env` contém segredos: **nunca** o envie por e-mail nem o publique.

## Etapa 4 — Criar o banco e o usuário

```bat
python manage.py criar_banco_postgres
```

O comando pergunta a **senha do usuário `postgres`** (a da etapa 1; ao digitar nada aparece na tela, é normal) e
então cria o usuário `DB_USER` e o banco `DB_NAME` definidos no `.env`. Resultado esperado:

```
Usuário 'escola_app' criado.
Banco 'escola' criado.
Pronto. Agora rode: python manage.py migrate
```

É seguro rodar de novo: se o usuário ou o banco já existirem, o comando só avisa e não altera nada.

## Etapa 5 — Criar as tabelas e o administrador do sistema

```bat
python manage.py migrate
python manage.py createsuperuser
```

- `migrate` cria todas as tabelas, os perfis de acesso e a tabela de séries. Leva alguns segundos e termina com
  linhas "OK". Pode ser repetido a qualquer momento (por exemplo, após uma atualização do sistema).
- `createsuperuser` cria o **administrador do sistema** (nome de usuário, e-mail opcional e senha). Esse usuário
  cadastra as escolas; ele não é o administrador de nenhuma escola.

## Etapa 6 — Iniciar e cadastrar a primeira escola

```bat
python manage.py runserver
```

1. Abra http://localhost:8000/ → clique em **Sou administrador do sistema** → entre com o usuário da etapa 5.
2. **Escolas → Nova escola**: preencha os dados da escola, envie o logotipo e informe o administrador da escola
   (nome, usuário e senha inicial).
3. Saia, escolha a escola na tela de login e entre com o usuário e a senha do administrador dela.

## Conferir os dados no pgAdmin (opcional)

Abra o **pgAdmin 4** → *Servers* → *PostgreSQL 16* (pede a senha do `postgres`) → *Databases* → `escola` →
*Schemas* → *public* → *Tables*. Clique com o botão direito em uma tabela → *View/Edit Data* → *All Rows*.
Evite editar dados direto pelo pgAdmin: use o sistema.

## Cópia de segurança (backup)

Faça backup **antes de atualizar o sistema** e periodicamente. No Prompt de Comando:

```bat
"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U escola_app -h localhost -F c -f backup_escola.dump escola
```

Ele pede a senha de `DB_USER`. Para restaurar em um banco vazio:

```bat
"C:\Program Files\PostgreSQL\16\bin\pg_restore.exe" -U escola_app -h localhost -d escola backup_escola.dump
```

Guarde os backups em outro disco ou na nuvem. Os logotipos e as fotos enviados ficam na pasta `media\` do
projeto: inclua-a no backup.

---

## Solução de problemas

| Mensagem ou sintoma | Causa provável | O que fazer |
|---|---|---|
| `Não foi possível conectar ao PostgreSQL` | Serviço parado, porta errada ou senha do `postgres` incorreta | Confira a Etapa 2 (serviço em execução), a porta em `DB_PORT` e digite de novo a senha do `postgres` |
| `password authentication failed for user "postgres"` | Senha do `postgres` incorreta | Use a senha definida na instalação (etapa 1) |
| `password authentication failed for user "escola_app"` | `DB_PASSWORD` diferente da senha usada quando o usuário foi criado | Corrija o `.env`. Se mudou a senha depois de criar o usuário, redefina-a: `ALTER ROLE escola_app PASSWORD 'nova';` no SQL Shell (psql) |
| `connection refused` / `could not connect to server` | PostgreSQL parado ou `DB_HOST`/`DB_PORT` errados | Inicie o serviço (Etapa 2). Se o instalador usou outra porta, ajuste `DB_PORT` |
| `UnicodeDecodeError ... 'utf-8' codec can't decode` ao conectar | O Windows em português devolve a mensagem de erro em outra codificação; quase sempre é senha errada ou serviço parado | Trate como erro de senha/conexão (linhas acima); confira também se `DB_PASSWORD` não tem acentos |
| `ModuleNotFoundError: No module named 'psycopg2'` | Dependências não instaladas ou ambiente virtual desativado | Rode `.venv\Scripts\activate` e `pip install -r requirements.txt` |
| `Defina DB_USER e DB_PASSWORD no arquivo .env` | `.env` incompleto | Preencha `DB_USER` e `DB_PASSWORD` (sem espaços ao redor do `=`) |
| `Defina DJANGO_SECRET_KEY ...` | Falta a chave no `.env` | Gere e cole a chave (Etapa 3) |
| `permission denied to create database` ao rodar `python manage.py test` | O usuário do sistema não pode criar o banco de testes | Só para quem roda testes: `ALTER ROLE escola_app CREATEDB;` no SQL Shell (psql) |
| `psql` não é reconhecido como comando | A pasta `bin` do PostgreSQL não está no PATH | Use o atalho **SQL Shell (psql)** do menu Iniciar ou o caminho completo `"C:\Program Files\PostgreSQL\16\bin\psql.exe"` |
| O sistema abriu, mas aparece "sem dados" depois de trocar de banco | O `.env` aponta para outro banco (ou voltou ao SQLite) | Confira `DB_NAME`; sem `DB_NAME` e `DATABASE_URL` o sistema usa o arquivo `db.sqlite3` |

Para saber qual banco o sistema está usando:

```bat
python manage.py shell -c "from django.db import connection as c; print(c.vendor, c.settings_dict['NAME'])"
```

Deve imprimir `postgresql escola`. Se imprimir `sqlite`, o `.env` não está sendo lido ou está sem `DB_NAME`.

## Resumo para Linux (Ubuntu/Debian)

```bash
sudo apt install postgresql postgresql-contrib libpq-dev
sudo systemctl enable --now postgresql
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'senha-do-postgres';"
# depois, na pasta do projeto, as etapas 3 a 6 acima (com source .venv/bin/activate)
```
