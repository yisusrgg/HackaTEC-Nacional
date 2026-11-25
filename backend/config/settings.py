"""
Configuración de Django del sistema de telemetría de carrocería.

Todos los secretos vienen de variables de entorno (archivo .env, que nunca se sube a git).
Genera uno con:  python scripts/generar_env.py
"""
import importlib.util
import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(nombre, defecto=False):
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _env_lista(nombre, defecto=""):
    return [x.strip() for x in os.environ.get(nombre, defecto).split(",") if x.strip()]


def _instalado(modulo):
    return importlib.util.find_spec(modulo) is not None


DEBUG = _env_bool("DJANGO_DEBUG")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("Falta DJANGO_SECRET_KEY. Genera el archivo .env con: python scripts/generar_env.py")

ALLOWED_HOSTS = _env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "auditoria",
    "telemetria",
    "tablero",
    "servicio",
    "confiabilidad",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *(["whitenoise.middleware.WhiteNoiseMiddleware"] if _instalado("whitenoise") else []),
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tablero.middleware.CabecerasSeguridadMiddleware",
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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Base de datos -------------------------------------------------------------------------------
# Se elige con DB_MOTOR en el .env: sqlite (por defecto), mysql o postgres. Para MySQL:
#   python scripts/generar_env.py --motor mysql --forzar
DB_MOTOR = os.environ.get("DB_MOTOR", "sqlite").strip().lower()
if DB_MOTOR == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",  # driver: mysqlclient
            "NAME": os.environ.get("DB_NOMBRE", "hackatecNacional"),
            "USER": os.environ.get("DB_USUARIO", "hackatecNacional"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PUERTO", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",  # acentos, "°" y cualquier carácter Unicode
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",  # rechaza datos inválidos en vez de truncarlos
            },
            "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
        }
    }
elif DB_MOTOR == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NOMBRE", "telemetria"),
            "USER": os.environ.get("DB_USUARIO", "telemetria"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PUERTO", "5432"),
        }
    }
elif DB_MOTOR != "sqlite":
    raise ImproperlyConfigured(f"DB_MOTOR='{DB_MOTOR}' no es válido: usa sqlite, mysql o postgres.")
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.environ.get("SQLITE_RUTA") or BASE_DIR / "db.sqlite3",
            "OPTIONS": {
                # WAL deja leer el tablero mientras llegan datos; IMMEDIATE evita "database is locked" al escribir.
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
                "transaction_mode": "IMMEDIATE",
                "timeout": 20,
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Autenticación y contraseñas -------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    *(["django.contrib.auth.hashers.Argon2PasswordHasher"] if _instalado("argon2") else []),
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
if sys.argv[1:2] == ["test"]:
    # Solo en pruebas: hash rápido para no esperar segundos por cada usuario creado.
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LOGIN_URL = "cuentas:login"
LOGIN_REDIRECT_URL = "tablero:inicio"  # cada rol entra a su pantalla (tablero/panorama.py)
LOGOUT_REDIRECT_URL = "cuentas:login"

# --- Sesiones, cookies y cabeceras de seguridad --------------------------------------------------
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

USAR_HTTPS = _env_bool("DJANGO_HTTPS")
SESSION_COOKIE_SECURE = USAR_HTTPS
CSRF_COOKIE_SECURE = USAR_HTTPS
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SEGUNDOS", "0"))
CSRF_TRUSTED_ORIGINS = _env_lista("DJANGO_CSRF_ORIGENES")

# --- Idioma y zona horaria -----------------------------------------------------------------------
LANGUAGE_CODE = "es-mx"
TIME_ZONE = os.environ.get("DJANGO_ZONA_HORARIA", "America/Mexico_City")
USE_I18N = True
USE_TZ = True

# --- Archivos estáticos (Tailwind compilado y Chart.js se sirven desde aquí, sin CDN) ------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True  # sirve /static sin correr collectstatic

# --- API (Django REST Framework) -----------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_THROTTLE_RATES": {"ingesta": "240/min", "hora": "30/min"},
}

# --- Propios del sistema -------------------------------------------------------------------------
CLAVE_CIFRADO_CAMPOS = os.environ.get("CLAVE_CIFRADO_CAMPOS", "")
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "panel-admin/")
TELEMETRIA = {
    "SEGUNDOS_EN_LINEA": 30,  # sin datos por más tiempo = dispositivo fuera de línea
    "MAC_FALLIDOS_PARA_ALERTA": 5,  # tramas con MAC inválido en 60 s que disparan alerta de seguridad
    "PUNTOS_MAX_SERIE": 1500,  # puntos máximos por gráfica (se promedian si hay más)
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"consola": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "telemetria": {"handlers": ["consola"], "level": "INFO"},
        "auditoria": {"handlers": ["consola"], "level": "INFO"},
    },
}
