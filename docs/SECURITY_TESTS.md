# EduNex — Testes de segurança

Executado em 15/11/2026, SQLite (desenvolvimento), `DJANGO_DEBUG=True`:

```
python manage.py test                 -> Ran 550 tests ... OK
python manage.py makemigrations --check --dry-run -> No changes detected
bandit -r apps config -c .bandit      -> severidade média/alta: 0 (1 falso positivo baixo anotado)
pip-audit -r requirements.txt         -> No known vulnerabilities found
check --deploy (env de produção)      -> no issues (W005/W021 silenciados de propósito: HSTS preload/subdomínios)
```

## Suítes de segurança
| Arquivo | O que prova |
|---------|-------------|
| `test_seguranca_auth.py` | Força bruta (conta e IP, bloqueio temporário e progressivo, conta inexistente indistinguível), política de senha/Argon2, ativação e recuperação por token (uso único, expiração, resposta igual), MFA (obrigatória, sem atalho, recuperação de uso único, limite de tentativas), sessão (logout, troca de chave), imutabilidade da auditoria |
| `test_seguranca_http.py`, `test_seguranca_web.py` | CSP com nonce sem `unsafe-inline`, nenhum handler inline nos templates, cabeçalhos, TRACE bloqueado, CSRF, `next` externo, XSS em documentos |
| `test_seguranca_uploads.py` | Imagem falsa recusada, EXIF removido, nome aleatório, limites, foto só autenticada |
| `test_multiescola.py` | Login por escola, isolamento de modelos, perfis, FKs entre escolas |
| **`test_seguranca_idor.py` (novo)** | Varredura IDOR/BOLA em todas as rotas com ID (GET+POST) |
| **`test_seguranca_lacunas.py` (novo)** | Webhook (falha, bloqueio, replay), acesso negado, tela de eventos por perfil, open redirect, retenção, rotação de chaves, trava de produção, exportação LGPD, alertas |
| `test_cobranca_bancaria.py` | Webhook autenticado, idempotência, isolamento entre escolas, cifragem de segredos |
| `test_financeiro.py`, `test_contas_pagar.py` | Integridade financeira (estorno, parcelas, saldo) |
| `test_assistente.py` | Limites de uso, ferramentas, remoção de dados pessoais e de nomes de pessoas da escola antes do envio |
| `test_seguranca_lacunas.py` (rodada complementar) | `AnonimizacaoDeNomesTests` (nomes mascarados; nomes de outra escola não entram) e `LimiteTaxaTests` (429 + `Retry-After`, teto de POST, isolamento entre usuários, anônimo por IP, estáticos, nova janela libera, desligável) |

## Limitações (o que estes testes NÃO provam)
* Rodaram em SQLite. Comportamentos de concorrência (`select_for_update`) e de PostgreSQL não foram exercitados.
* A varredura IDOR usa IDs 1–15 e o perfil Administrador da Escola B; não substitui revisão de cada rota nem pentest.
* Não houve teste dinâmico (OWASP ZAP), Semgrep (regras inacessíveis pelo proxy do ambiente) nem Gitleaks (binário indisponível); houve só varredura por expressões regulares.
* O limitador de requisições é desligado ao rodar `manage.py test` e ligado apenas nos seus próprios testes.
* Concorrência real em pagamentos duplicados não foi testada com múltiplos processos.
* MFA/e-mail/HTTPS/proxy dependem do ambiente de produção.
