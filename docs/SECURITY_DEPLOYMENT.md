# EduNex — Implantação segura em produção

Pilha recomendada: Nginx (HTTPS) → Gunicorn (Unix socket) → Django → PostgreSQL local ou gerenciado.
Nunca use `runserver` em produção.

## 1. Variáveis de ambiente (`.env`, permissão 600, fora do git)
| Variável | Obrigatória | Valor |
|----------|-------------|-------|
| `DJANGO_DEBUG` | sim | `False` |
| `DJANGO_SECRET_KEY` | sim | 50+ caracteres aleatórios, exclusiva do ambiente (o sistema **não inicia** com chave curta) |
| `DJANGO_ALLOWED_HOSTS` | sim | domínios separados por vírgula, sem `*` (o sistema **não inicia** sem isso) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | sim | `https://escola.exemplo.com.br` |
| `DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT` | sim | usuário **sem** superuser (ver §4) |
| `DJANGO_SITE_URL` | sim | `https://escola.exemplo.com.br` (URL do webhook) |
| `DJANGO_PROXY_HTTPS` | sim | `True` atrás do Nginx |
| `DJANGO_SSL_REDIRECT` | sim | `True` |
| `DJANGO_SECURE_COOKIES` | padrão `True` | não desligar |
| `DJANGO_HSTS_SEGUNDOS` | após validar HTTPS | comece com `3600`; depois `31536000` |
| `DJANGO_HSTS_SUBDOMINIOS`, `DJANGO_HSTS_PRELOAD` | não | só ligue após auditar **todos** os subdomínios |
| `DJANGO_CONFIAR_PROXY` | sim atrás de proxy | `True` (usa o último `X-Forwarded-For`, o do seu Nginx); sem isso o limite por IP trata todos como o mesmo IP |
| `BILLING_CRYPTO_KEY` | sim | chave Fernet própria (ver §7) |
| `DJANGO_LIMITE_REQ_USUARIO_MIN`, `DJANGO_LIMITE_POST_USUARIO_MIN`, `DJANGO_LIMITE_REQ_ANONIMO_MIN` | opcional | Limite de requisições por minuto na aplicação (padrão 300 / 90 / 300). Atrás de proxy ligue `DJANGO_CONFIAR_PROXY`; sem isso todos os anônimos contam como um único IP. Com vários workers o cache padrão é por processo; use Redis/Memcached para limite global |
| `DJANGO_MFA_OBRIGATORIA` | padrão `True` em produção | manter |
| `EMAIL_HOST…` | recomendado | necessário para links de ativação/recuperação por e-mail |
| `DJANGO_CSP_SOMENTE_RELATAR` | só em testes | `True` apenas ao validar uma CSP nova |

Gere a SECRET_KEY: `python -c "from django.core.management.utils import get_random_secret_key as g; print(g()*2)"`.

## 2. Nginx (HTTPS, limites e arquivos)
```nginx
limit_req_zone $binary_remote_addr zone=geral:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;

server {
    listen 443 ssl http2;
    server_name escola.exemplo.com.br;
    ssl_certificate     /etc/letsencrypt/live/escola.exemplo.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/escola.exemplo.com.br/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 6m;           # fotos até 5 MB

    location /static/ { alias /srv/edunex/staticfiles/; }
    # SOMENTE logotipos são públicos. Fotos de alunos NÃO devem ser expostas: o sistema as entrega por tela autenticada.
    location /media/logotipos/ { alias /srv/edunex/media/logotipos/; }
    location /media/ { return 404; }

    location = /entrar/ { limit_req zone=login burst=5 nodelay; proxy_pass http://unix:/run/edunex.sock; include proxy_params_edunex; }
    location / {
        limit_req zone=geral burst=40 nodelay;
        proxy_pass http://unix:/run/edunex.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;   # sobrescreve: o cliente não consegue forjar
    }
}
server { listen 80; server_name escola.exemplo.com.br; return 301 https://$host$request_uri; }
```
A CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy` e HSTS são enviados pela aplicação; não duplique no Nginx.

## 3. Gunicorn (systemd)
`gunicorn config.wsgi --bind unix:/run/edunex.sock --workers 3 --timeout 30 --max-requests 1000`, com usuário de serviço sem
login, `EnvironmentFile=/srv/edunex/.env` e `ProtectSystem=strict`, `NoNewPrivileges=true`.

## 4. PostgreSQL
* Crie um papel exclusivo da aplicação **sem** `SUPERUSER`, `CREATEDB` ou `CREATEROLE`, dono apenas do banco do sistema.
* `pg_hba.conf`: aceitar somente o IP da aplicação, `scram-sha-256`; `ssl = on` e `hostssl` quando o banco for remoto.
* Senha forte e única; nunca no código.
* Backup diário cifrado, com cópia **fora do servidor**:
  `pg_dump -Fc edunex | gpg --encrypt -r backup@escola > edunex-$(date +%F).dump.gpg`; guarde também `.env` e `media/` (separadamente das chaves).
  Retenção sugerida: 30 diários + 12 mensais. **Teste a restauração** em um banco de teste ao menos trimestralmente.
* Monitore conexões (`pg_stat_activity`) e espaço em disco.

## 5. Rotinas agendadas (cron do usuário de serviço)
```
0 3 * * *   python manage.py limpar_seguranca          # retenção (tentativas, tokens, conversas de IA, eventos antigos)
0 * * * *   python manage.py alertas_seguranca         # e-mail aos administradores do sistema se houver limites excedidos
*/30 * * * * python manage.py conciliar_cobrancas      # rede de segurança dos boletos
```
`limpar_seguranca --simular` mostra o que seria removido. **Auditoria (`RegistroAuditoria`) e documentos nunca são apagados.**

## 6. Cookies, HSTS e CSP
* Cookies de sessão e CSRF: `Secure`, `HttpOnly`, `SameSite=Lax`; sessão de 12 h.
* HSTS: comece com 1 hora; só aumente depois de confirmar que **todo** o site funciona em HTTPS. Não habilite
  `includeSubDomains`/`preload` sem checar todos os subdomínios.
* CSP: nonce por resposta, `script-src` sem `unsafe-inline`, `object-src 'none'`, `base-uri 'self'`,
  `frame-ancestors 'none'`, `upgrade-insecure-requests` em produção. Se acrescentar um CDN, inclua o domínio em `DJANGO_CSP`
  testando antes com `DJANGO_CSP_SOMENTE_RELATAR=True`.

## 7. Rotação de credenciais
* **Chave do cofre (Fernet):** gere nova (`Fernet.generate_key()`); em `BILLING_CRYPTO_KEY` use `NOVA,ANTIGA`; reinicie;
  `python manage.py rotacionar_segredos`; confira; remova a `ANTIGA`. Guarde as chaves em local separado do banco. **Perder a
  chave obriga a reinformar chaves de API e a refazer o MFA dos usuários.**
* **SECRET_KEY:** invalida sessões e tokens pendentes; planeje fora do horário e comunique.
* **Chave de API do banco/IA:** troque na tela de configuração (a nova é cifrada na hora); revogue a antiga no provedor.
* **Token do webhook:** botão "renovar token" na tela de cobrança; atualize no painel do banco.
* **Credencial vazada:** revogue no provedor, rotacione, revise a tela *Eventos de segurança* e a Auditoria.

## 8. Monitoramento
Tela **Eventos de segurança** (administrador do sistema e da escola), `alertas_seguranca` por e-mail, logs do Nginx/Gunicorn
(sem corpo de requisição), alerta de disco/CPU/certificado a vencer, e `check --deploy` a cada release.

## 9. Checklist de publicação
- [ ] `.env` com as variáveis acima; permissão 600; fora do git
- [ ] `python manage.py migrate` e `collectstatic`
- [ ] `check --deploy` sem avisos (com HSTS/SSL ligados)
- [ ] HTTPS válido; `http` redireciona; HSTS validado
- [ ] Nginx expondo apenas `/static/` e `/media/logotipos/`
- [ ] Usuário do banco sem superuser; backup cifrado e restauração testada
- [ ] Administrador do sistema com MFA ativada
- [ ] Cron de `limpar_seguranca`, `alertas_seguranca` e `conciliar_cobrancas`
- [ ] E-mail de saída testado (ativação de conta e recuperação)
- [ ] `pip-audit` e testes passando no CI
