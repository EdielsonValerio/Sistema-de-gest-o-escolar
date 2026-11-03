"""Configurações do projeto. Valores sensíveis vêm de variáveis de ambiente (.env)."""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv é opcional em produção
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", False)

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        # Chave descartável apenas para desenvolvimento local (nunca usar em produção).
        SECRET_KEY = "dev-only-insecure-key-" + "x" * 40
    else:
        raise ImproperlyConfigured(
            "Defina DJANGO_SECRET_KEY no ambiente/.env (ou DJANGO_DEBUG=True em desenvolvimento)."
        )

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.students",
    "apps.academic",
    "apps.enrollments",
    "apps.finance",
    "apps.audit",
    "apps.documents",
    "apps.payables",
    "apps.billing",
    "apps.assistente",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SegurancaMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "apps.core.middleware.EscolaMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.instituicao",
                "apps.payables.context_processors.notificacoes_contas_pagar",
                "apps.assistente.context_processors.assistente",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def _database():
    """PostgreSQL (recomendado/produção) por DB_* ou DATABASE_URL; sem nenhum dos dois, SQLite local (só dev/testes)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    nome = os.environ.get("DB_NAME", "").strip()
    if url:
        from urllib.parse import unquote, urlparse

        p = urlparse(url)
        if p.scheme not in ("postgres", "postgresql"):
            raise ImproperlyConfigured("DATABASE_URL deve usar o esquema postgres:// ou postgresql://")
        cfg = {"NAME": p.path.lstrip("/"), "USER": unquote(p.username or ""), "PASSWORD": unquote(p.password or ""),
               "HOST": p.hostname or "localhost", "PORT": p.port or 5432}
    elif nome:
        cfg = {"NAME": nome, "USER": os.environ.get("DB_USER", "postgres"),
               "PASSWORD": os.environ.get("DB_PASSWORD", ""), "HOST": os.environ.get("DB_HOST", "localhost"),
               "PORT": os.environ.get("DB_PORT", "5432")}
    else:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # IMMEDIATE evita deadlock de upgrade de transação em escritas concorrentes (sequências).
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20},
            "TEST": {"NAME": BASE_DIR / "test_db.sqlite3"},
        }
    return {"ENGINE": "django.db.backends.postgresql", "CONN_MAX_AGE": 60, **cfg}


DATABASES = {"default": _database()}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Fortaleza"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EscolaBackend",  # escola + login + senha (usuários das escolas)
    "django.contrib.auth.backends.ModelBackend",  # username global: somente administrador do sistema/admin do Django
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_SEGUNDOS", 60 * 60 * 12))  # 12 h

if not DEBUG:
    SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", False)
    # HSTS: só ligue depois de confirmar que o site funciona 100% em HTTPS (o navegador memoriza a regra).
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SEGUNDOS", 0))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_SUBDOMINIOS", False)
    SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
    if env_bool("DJANGO_PROXY_HTTPS", False):  # atrás de Nginx/ALB que repassa X-Forwarded-Proto
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Content-Security-Policy. As telas usam Bootstrap/Chart.js do jsDelivr e pequenos scripts/estilos embutidos,
# por isso 'unsafe-inline' (endurecer com nonce é uma evolução futura). O restante fica restrito.
CONTENT_SECURITY_POLICY = os.environ.get("DJANGO_CSP") or "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
    "font-src 'self' https://cdn.jsdelivr.net data:",
    "img-src 'self' data: blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
CSP_REPORT_ONLY = env_bool("DJANGO_CSP_SOMENTE_RELATAR", False)

# Evita registrar dados pessoais em logs: nenhum log de formulário/POST é configurado.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}

# Endereço público (HTTPS) do sistema: usado para exibir a URL do webhook da cobrança bancária.
SITE_URL = os.environ.get("DJANGO_SITE_URL", "").strip().rstrip("/")
