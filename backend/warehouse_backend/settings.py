from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')


def env_bool(name: str, default: str = '0') -> bool:
    return os.getenv(name, default).strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name: str, default: str = '') -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(',') if item.strip()]


DEBUG = env_bool('DEBUG', '0')
SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-local-development-only'
    else:
        raise ImproperlyConfigured('生产环境必须通过 SECRET_KEY 配置随机密钥。')
if not DEBUG and (
    SECRET_KEY.startswith('django-insecure-')
    or SECRET_KEY in {'replace-me', 'change-me'}
    or len(set(SECRET_KEY)) < 5
):
    raise ImproperlyConfigured('生产环境不能使用开发占位 SECRET_KEY。')
ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', '127.0.0.1,localhost')
USE_HTTPS = env_bool('USE_HTTPS', '0')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework.authtoken',
    'corsheaders',
    'rest_framework',
    'apps.common.apps.CommonConfig',
    'apps.accounts.apps.AccountsConfig',
    'apps.inventory.apps.InventoryConfig',
    'apps.workflow.apps.WorkflowConfig',
    'apps.notifications.apps.NotificationsConfig',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'warehouse_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.common.context_processors.web_navigation',
            ],
        },
    },
]

WSGI_APPLICATION = 'warehouse_backend.wsgi.application'

database_engine = os.getenv('DB_ENGINE', 'django.db.backends.sqlite3')
database_name = os.getenv('DB_NAME', str(BASE_DIR / 'db.sqlite3'))
if database_engine == 'django.db.backends.sqlite3':
    database_path = Path(database_name).expanduser()
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path
    database_name = str(database_path)

DATABASES = {
    'default': {
        'ENGINE': database_engine,
        'NAME': database_name,
        'USER': os.getenv('DB_USER', ''),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', ''),
        'PORT': os.getenv('DB_PORT', ''),
    }
}

if DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3':
    DATABASES['default'].pop('USER', None)
    DATABASES['default'].pop('PASSWORD', None)
    DATABASES['default'].pop('HOST', None)
    DATABASES['default'].pop('PORT', None)
    DATABASES['default']['OPTIONS'] = {
        'timeout': int(os.getenv('SQLITE_TIMEOUT_SECONDS', '30')),
    }
    DATABASES['default']['CONN_MAX_AGE'] = 0

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = os.getenv('TIME_ZONE', 'Asia/Shanghai')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = os.getenv('STATIC_ROOT', str(BASE_DIR / 'staticfiles'))
MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL_ORIGINS', '0')
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', '')
SECURE_SSL_REDIRECT = USE_HTTPS
SESSION_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_SECURE = USE_HTTPS
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if USE_HTTPS else None
SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '0')) if USE_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', '0') if USE_HTTPS else False
SECURE_HSTS_PRELOAD = False

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'apps.common.token_authentication.ExpiringTokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

DINGTALK_WEBHOOK = os.getenv('DINGTALK_WEBHOOK', '')
DINGTALK_SECRET = os.getenv('DINGTALK_SECRET', '')
DINGTALK_TIMEOUT_SECONDS = int(os.getenv('DINGTALK_TIMEOUT_SECONDS', '5'))
DINGTALK_APP_KEY = os.getenv('DINGTALK_APP_KEY', '')
DINGTALK_APP_SECRET = os.getenv('DINGTALK_APP_SECRET', '')
DINGTALK_AGENT_ID = os.getenv('DINGTALK_AGENT_ID', '')
RESERVATION_EXPIRY_HOURS = int(os.getenv('RESERVATION_EXPIRY_HOURS', '72'))
CONSISTENCY_STATE_FILE = os.getenv(
    'CONSISTENCY_STATE_FILE',
    str(BASE_DIR / 'data' / 'consistency-check.state.json'),
)

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# 登录失败限流默认适合内部系统的小范围试用，可通过环境变量调整。
LOGIN_RATE_LIMIT_FAILURES = int(os.getenv('LOGIN_RATE_LIMIT_FAILURES', '5'))
LOGIN_RATE_LIMIT_IP_FAILURES = int(os.getenv('LOGIN_RATE_LIMIT_IP_FAILURES', '20'))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('LOGIN_RATE_LIMIT_WINDOW_SECONDS', '300'))
LOGIN_RATE_LIMIT_LOCKOUT_SECONDS = int(os.getenv('LOGIN_RATE_LIMIT_LOCKOUT_SECONDS', '900'))
API_RATE_LIMIT_IP_REQUESTS = int(os.getenv('API_RATE_LIMIT_IP_REQUESTS', '30'))
API_RATE_LIMIT_ACCOUNT_REQUESTS = int(os.getenv('API_RATE_LIMIT_ACCOUNT_REQUESTS', '10'))
API_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('API_RATE_LIMIT_WINDOW_SECONDS', '60'))
API_TOKEN_TTL_SECONDS = int(os.getenv('API_TOKEN_TTL_SECONDS', '2592000'))
SECURITY_STATE_RETENTION_SECONDS = int(os.getenv('SECURITY_STATE_RETENTION_SECONDS', '7776000'))
