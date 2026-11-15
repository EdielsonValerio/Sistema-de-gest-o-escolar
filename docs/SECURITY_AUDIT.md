# EduNex — Relatório de auditoria de segurança

Data: 15/11/2026 · Escopo: código-fonte do repositório (Django 5.2, 13 apps) · Método: leitura do código, análise
estática (Bandit), auditoria de dependências (pip-audit), `check --deploy`, varredura automatizada de isolamento entre
escolas e suíte de testes (550 testes, todos aprovados; 541 na rodada da manhã e 9 acrescentados na rodada complementar).

> Este documento só afirma o que foi **reproduzido ou verificado tecnicamente**. Itens que dependem de servidor,
> rede ou auditoria externa estão em "Riscos residuais" — não foram verificados aqui.

## 1. Estado encontrado antes desta rodada

O repositório já trazia uma camada de segurança relevante (verificada por leitura e pelos testes existentes):
login por escola+usuário+senha com bloqueio por conta e por IP (progressivo 15 min → 4 h, sempre temporário);
senhas Argon2 com mínimo de 12 caracteres; ativação de conta e recuperação de senha por token de uso único (hash
SHA-256, validade 3 dias / 1 h, sem envio de senha por e-mail); MFA TOTP com códigos de recuperação e obrigatoriedade
para Administrador, Financeiro e administrador do sistema; sessão trocada no login e invalidada em redefinição;
CSP com nonce (sem `unsafe-inline` em scripts); cabeçalhos de segurança; upload de imagens reprocessado (Pillow,
sem EXIF, nome aleatório, limites de tamanho/pixels); fotos de alunos só por tela autenticada; segredos
(chaves de API, token de webhook, segredo TOTP) cifrados com Fernet; webhook com token comparado em tempo constante
e eventos idempotentes; sanitização do HTML dos modelos de documento; auditoria imutável; ORM em todo o projeto
(nenhum SQL manual encontrado).

## 2. Achados desta rodada

Classificação: **Confirmado** = reproduzido/verificado no código ou por teste. Nenhum achado Crítico ou Alto foi
confirmado.

| # | Sev. | Achado (confirmado) | Local | Correção | Teste |
|---|------|---------------------|-------|----------|-------|
| 1 | Médio | O comando `limpar_seguranca`, citado no código como responsável por limpar as falhas de login, **não existia**: `TentativaLogin` crescia sem limite (enchimento do banco por requisições anônimas) e tokens/conversas de IA nunca expiravam. | `apps/accounts/models.py`, ausência de comando | Criado `limpar_seguranca` (tentativas 2 d, tokens vencidos 30 d, conversas de IA 180 d, eventos 400 d; **nunca** apaga auditoria) | `RetencaoTests` |
| 2 | Médio | Falhas de autenticação do **webhook bancário** não eram registradas nem limitadas: o tipo de evento existia mas nunca era gravado; um atacante podia tentar tokens indefinidamente. | `apps/billing/views.py` | Falha grava `WEBHOOK_FALHA`; 10 falhas por IP bloqueiam (429) com espera progressiva; sucesso zera | `WebhookEventosTests` |
| 3 | Médio | **Acessos negados (403) não geravam evento** (`ACESSO_NEGADO` definido e nunca emitido), impossibilitando detectar sondagem de permissões. | `apps/core/mixins.py` | 403 de usuário autenticado vira evento (apenas método+caminho, nunca query string; teto de 20/usuário/hora) | `AcessoNegadoTests` |
| 4 | Médio | Eventos de segurança **sem tela e sem alerta**: ninguém via os bloqueios. | — | Tela `Eventos de segurança` (admin do sistema: todas as escolas; admin da escola: a sua) e comando `alertas_seguranca` (e-mail aos administradores do sistema acima de limites e a cada ação sensível) | `AcessoNegadoTests`, `AlertasTests` |
| 5 | Baixo | **Open redirect** no parâmetro `next` de duas ações da cobrança: `startswith("/")` aceita `/\evil.com`, que navegadores tratam como `//evil.com`. Exigia POST autenticado com CSRF (por isso Baixo). | `apps/billing/views.py` | Validação com `url_has_allowed_host_and_scheme` | `OpenRedirectTests` |
| 6 | Médio | **Sem procedimento de rotação** da chave do cofre (Fernet): trocar a chave tornava os segredos ilegíveis. | `apps/billing/crypto.py` | `BILLING_CRYPTO_KEY` aceita várias chaves (a primeira cifra, todas decifram) + comando `rotacionar_segredos` (cobre banco, IA e MFA) | `RotacaoDeChavesTests` |
| 7 | Médio | Produção podia subir com **configuração de desenvolvimento** silenciosamente: `ALLOWED_HOSTS` com padrão `localhost` e `SECRET_KEY` curta eram aceitos com `DEBUG=False`. | `config/settings.py` | Com `DEBUG=False` o sistema **não inicia** sem `DJANGO_ALLOWED_HOSTS` explícito (sem `*`) e sem chave de 50+ caracteres | `TravaDeProducaoTests` |
| 8 | Baixo | Sem limites explícitos de tamanho de requisição. | `config/settings.py` | `DATA_UPLOAD_MAX_MEMORY_SIZE` 5 MB; `DATA_UPLOAD_MAX_NUMBER_FIELDS` 3000; permissão 0640 em arquivos enviados | `check --deploy` |
| 9 | Baixo | **LGPD**: não havia exportação dos dados pessoais de um titular. | — | Botão "Dados do titular" na ficha (permissão própria `exportar_dados_titular`, só Administrador): JSON sem segredos, `Cache-Control: no-store`, sempre auditado | `ExportacaoLgpdTests` |
| 10 | Baixo | `assert` usado como validação em produção (removido com `python -O`). | `apps/billing/codigos.py` | Substituído por `ValueError` explícito | suíte de cobrança |
| 11 | Informativo | Teste de impressão do boletim dependia de JavaScript inline (incompatível com a CSP). | `tests/test_boletim.py` | Teste ajustado para `data-print`/`data-autoprint` | `ImprimirTests` |
| 13 | Médio | **Nomes de pessoas em texto livre** digitados na pergunta ao assistente de IA eram enviados ao fornecedor (só CPF, CNPJ, e-mail e telefone eram removidos). | `apps/assistente/lgpd.py`, `servicos.py` | Os nomes de alunos, pais, responsáveis e usuários **da própria escola** são mascarados (`[nome removido]`) antes do envio e do armazenamento: nome completo ou dois primeiros nomes, sem diferenciar acento, caixa ou espaços; nomes de uma palavra só não entram (confundiriam palavras comuns) | `AnonimizacaoDeNomesTests`, `ServicosTests` |
| 14 | Médio | **Sem limite de requisições** para usuários autenticados e anônimos na aplicação (só login, recuperação, webhook e pré-matrícula tinham): abuso de endpoint e negação de serviço de nível de aplicação dependiam só do Nginx. | `config/settings.py`, `apps/core/middleware.py` | `LimiteTaxaMiddleware`: 300 req/min e 90 POST/min por usuário, 300 req/min por IP anônimo (ajustáveis por variável de ambiente); resposta 429 com `Retry-After`; evento `LIMITE_TAXA` (1 por janela) com alerta no comando `alertas_seguranca`; estáticos não contam | `LimiteTaxaTests` |
| 12 | Info | Ausência de CI de segurança, `.gitignore` incompleto, sem `.env.example` de MFA/rotação. | repositório | Workflow `seguranca.yml` (testes, migrações, `check --deploy`, Bandit, pip-audit, Gitleaks, Semgrep), `.gitignore`, `.bandit`, `.gitleaks.toml`, `requirements-dev.txt` | — |

### Verificações que **não** encontraram falha
* **Isolamento entre escolas (IDOR/BOLA):** `tests/test_seguranca_idor.py` faz o administrador da Escola B requisitar,
  por GET e POST (com IDs da Escola A no corpo), **todas as rotas com parâmetro numérico** (IDs 1–15; mais de 300
  requisições). Resultado: nenhuma resposta com dado da Escola A, nenhuma alteração de dados da Escola A (comparação
  por serialização antes/depois) e nenhum registro da Escola B apontando para a Escola A.
  Limite: cobre IDs 1–15 e o perfil Administrador; não é prova formal.
* **SQL injection:** nenhum SQL manual (`raw`, `cursor`, `extra`, `RawSQL`) no código de aplicação (busca por texto, sem ocorrências).
* **XSS:** `mark_safe` aparece em 4 pontos, todos sobre HTML/SVG montado pelo sistema com `escape()` ou só com
  números (Bandit B308/B703 revisados e anotados com `nosec` justificado). O único `|safe` em template é o HTML do
  documento emitido: o modelo é sanitizado ao salvar e os valores são escapados ao emitir (coberto por `XssDocumentosTests`).
* **SSRF:** as duas únicas chamadas HTTP de saída (Asaas e Anthropic) usam URL fixa `https://`; nenhum destino vem de
  entrada do usuário.
* **Webhook:** token em `hmac.compare_digest`, corpo limitado a 256 KB, evento idempotente por `(provedor, evento_id)`
  (replay devolve "duplicado" sem nova baixa) e pagamento reconfirmado direto no banco antes da baixa.
* **Dependências:** `pip-audit -r requirements.txt` → nenhuma vulnerabilidade conhecida (consulta de 15/11/2026).
* **Bandit** (severidade média+): 0 achados após revisão; 1 falso positivo de baixa severidade anotado.

## 3. Contagem

| Severidade | Encontradas | Corrigidas | Pendentes |
|------------|-------------|------------|-----------|
| Crítica | 0 | 0 | 0 |
| Alta | 0 | 0 | 0 |
| Média | 8 | 8 | 0 |
| Baixa | 4 | 4 | 0 |
| Informativa | 2 | 2 | 0 |

## 4. Não corrigido / riscos residuais
Ver `docs/SECURITY_TESTS.md` (seção "Limitações") e `docs/SECURITY_POLICIES.md`. Resumo:
1. `style-src-attr 'unsafe-inline'` permanece na CSP (atributos `style=""` do layout); não executa código.
2. A anonimização do assistente de IA remove CPF, CNPJ, e-mail, telefone e **nomes cadastrados na escola**. Nomes de
   pessoas **que não estão cadastradas** (ex.: um terceiro citado) e nomes de uma palavra só, ou escritos com erro de
   digitação, **não são detectados**. Orientar os usuários a não digitarem dados pessoais.
3. O limite de requisições da aplicação usa o cache do Django: com o cache padrão (memória do processo) vale **por
   processo do gunicorn** (o limite efetivo é maior com vários workers). Para limite global use Redis/Memcached; o
   `limit_req` do Nginx continua recomendado (ver `SECURITY_DEPLOYMENT.md`).
4. Antivírus: o sistema só aceita **imagens** (reprocessadas pela Pillow); não há upload de documentos arbitrários,
   portanto não se aplica hoje.
5. **Semgrep**: instalado, mas não baixou as regras (acesso a `semgrep.dev` bloqueado neste ambiente) — não executado.
   **Gitleaks** (binário) e **OWASP ZAP** não estão disponíveis aqui — não executados; estão no CI. Como substituto parcial
   foi feita uma varredura por expressões regulares (chaves AWS, chaves privadas, tokens Anthropic/GitHub/Asaas, em
   arquivos e em todo o histórico Git): nenhuma ocorrência; `.env`, `db.sqlite3` e chaves não estão versionados. Isso
   **não equivale** a rodar o Gitleaks.
8. Os perfis **Direção** e **Responsável** citados na especificação **não existem** no sistema (perfis atuais:
   Administrador, Secretaria, Financeiro, Consulta, Professor, Coordenador). Não há portal do responsável; se for criado,
   deve passar por nova auditoria de autorização.
9. Fuso e concorrência: os testes rodaram em SQLite; condições de corrida em pagamentos reais (PostgreSQL, vários
   processos) não foram exercitadas.
6. Backup, TLS do PostgreSQL, proxy, certificados, monitoramento e teste de restauração são de infraestrutura e não
   foram verificados.
7. O IP real do cliente depende de `DJANGO_CONFIAR_PROXY` estar correto atrás do proxy; se mal configurado, o limite
   por IP agrupa usuários (ver `SECURITY_DEPLOYMENT.md`).
