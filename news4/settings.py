"""
Django settings for news4 project.
"""

from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
STATIC_ROOT = BASE_DIR / "staticfiles"
MODEL_ARTIFACTS_DIR = BASE_DIR / "model_artifacts"

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-secret-key-change-me")

DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_q",
    "news",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "news4.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [TEMPLATES_DIR],
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

WSGI_APPLICATION = "news4.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [STATIC_DIR]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_REDIRECT_URL = "newsapp:newspage"
LOGOUT_REDIRECT_URL = "newsapp:welcome"
LOGIN_URL = "newsapp:login"

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

USE_FINBERT = os.environ.get("USE_FINBERT", "1").strip() == "1"
ENABLE_LIVE_PREDICTION = os.environ.get("ENABLE_LIVE_PREDICTION", "0").strip() == "1"
ENABLE_NEWS_SENTIMENT_ON_PAGE = os.environ.get("ENABLE_NEWS_SENTIMENT_ON_PAGE", "0").strip() == "1"
ENABLE_COMPANY_PROFILE_FETCH = os.environ.get("ENABLE_COMPANY_PROFILE_FETCH", "1").strip() == "1"
ALLOW_PRICE_NETWORK_FALLBACK = os.environ.get("ALLOW_PRICE_NETWORK_FALLBACK", "0").strip() == "1"

Q_CLUSTER = {
    "name": "DjangORM",
    "workers": 1,
    "timeout": 120,
    "retry": 180,
    "queue_limit": 50,
    "bulk": 1,
    "orm": "default",
}