from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .settings_guards import ProductionConfigError, validate_production_config
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

# ── 生产环境误配置硬保护 ──────────────────────────────────────────────
#
# `.env.example` 里为了方便本机开发写了 ALLOWED_HOSTS 含 `*`。手工部署时
# 直接复制成 .env 会让任意 Host 头通过校验（Host 头攻击 / 缓存投毒），
# 且这类错误在界面上完全看不出来。SECRET_KEY 已有硬校验，这里补齐其余三项。
# 校验规则抽到 settings_guards.py，便于单测覆盖（在 settings 里 raise 无法断言）。
if not DEBUG:
    try:
        validate_production_config(
            debug=DEBUG,
            allowed_hosts=ALLOWED_HOSTS,
            cors_allow_all=env_bool('CORS_ALLOW_ALL_ORIGINS', '0'),
        )
    except ProductionConfigError as exc:
        raise ImproperlyConfigured(str(exc)) from exc

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
    'apps.common.middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.accounts.middleware.AccountSessionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'apps.common.middleware.NoStoreHtmlMiddleware',
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
# SQLite must live in a directory where it can create and delete journal files.
# The repository root can carry source-protection ACLs on Windows, so local data
# belongs under LOCALAPPDATA. Linux production supplies PostgreSQL settings.
if os.name == 'nt':
    default_sqlite_name = (
        Path(os.getenv('LOCALAPPDATA', BASE_DIR.parent))
        / 'AfterSalesWarehouse' / 'data' / 'db.sqlite3'
    )
else:
    default_sqlite_name = BASE_DIR.parent / 'runtime-data' / 'db.sqlite3'
database_name = os.getenv('DB_NAME', '').strip() or str(default_sqlite_name)
if database_engine == 'django.db.backends.sqlite3':
    database_path = Path(database_name).expanduser()
    if not database_path.is_absolute():
        database_path = BASE_DIR / database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
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

# 连接复用：每请求重建 PostgreSQL 连接要额外 5~10ms，内网实测占比可观。
# PostgreSQL 允许长连接，设为 60 秒；超过 60 秒的空闲连接由 Django 主动关闭，
# 避免被数据库端或防火墙静默断开后拿到废连接。
# SQLite 不支持长连接复用，保持 0（上面的分支已显式设过）。
if DATABASES['default']['ENGINE'] != 'django.db.backends.sqlite3':
    DATABASES['default']['CONN_MAX_AGE'] = int(os.getenv('DB_CONN_MAX_AGE', '60'))
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True

# ---------------------------------------------------------------- 模板缓存
#
# 默认 Django 每个请求都会重新读取并编译模板文件；本项目 web_base.html 有 1200+ 行
# 内联 CSS，重复解析开销明显。启用 cached loader 后模板只编译一次，进程内复用。
# DEBUG 下必须关闭，否则改模板要重启才生效，开发体验变差。
# DEBUG 下关闭模板缓存，否则改模板要重启才生效。
# 注意：一旦在 OPTIONS 里定义 loaders，Django 不允许同时保留 APP_DIRS=True，
# 所以启用缓存时必须把 APP_DIRS 置为 False，否则启动即报
# "app_dirs must not be set when loaders is defined"。
if not DEBUG:
    TEMPLATES[0]['APP_DIRS'] = False
    TEMPLATES[0]['OPTIONS']['loaders'] = [
        ('django.template.loaders.cached.Loader', [
            'django.template.loaders.filesystem.Loader',
            'django.template.loaders.app_directories.Loader',
        ]),
    ]

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
# 打印标签用的 logo 等少量静态资源放在 backend/static 下。
STATICFILES_DIRS = [str(BASE_DIR / 'static')]
MEDIA_URL = '/media/'
MEDIA_ROOT = os.getenv('MEDIA_ROOT', str(BASE_DIR / 'media'))

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOW_ALL_ORIGINS = env_bool('CORS_ALLOW_ALL_ORIGINS', '0')
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', '')

# === 结构化日志配置 ===
# 生产环境由 systemd/journald 收集 stdout/stderr，无需写文件
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name} {module}.{funcName}:{lineno} - {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': os.getenv('LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO'),
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': os.getenv('APP_LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO'),
            'propagate': False,
        },
    },
}

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
# 审批通过后预约的保留时长，默认 7 天。留空会让库存被无限期锁定。
RESERVATION_APPROVED_EXPIRY_HOURS = int(os.getenv('RESERVATION_APPROVED_EXPIRY_HOURS', '168'))
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

# 反向代理白名单：只有 REMOTE_ADDR 命中这里，才采信 X-Real-IP / X-Forwarded-For。
# 生产环境由同机 nginx 反代，因此默认只信任回环地址；直连部署时留空更安全。
TRUSTED_PROXY_IPS = env_list('TRUSTED_PROXY_IPS', '127.0.0.1,::1')
API_RATE_LIMIT_IP_REQUESTS = int(os.getenv('API_RATE_LIMIT_IP_REQUESTS', '30'))
API_RATE_LIMIT_ACCOUNT_REQUESTS = int(os.getenv('API_RATE_LIMIT_ACCOUNT_REQUESTS', '10'))
API_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('API_RATE_LIMIT_WINDOW_SECONDS', '60'))
API_TOKEN_TTL_SECONDS = int(os.getenv('API_TOKEN_TTL_SECONDS', '2592000'))
# 申请页扫码查询限流：既保证正常扫码不被打断，又限制批量枚举台账。
SCAN_RATE_LIMIT_REQUESTS = int(os.getenv('SCAN_RATE_LIMIT_REQUESTS', '120'))
SCAN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('SCAN_RATE_LIMIT_WINDOW_SECONDS', '300'))
SECURITY_STATE_RETENTION_SECONDS = int(os.getenv('SECURITY_STATE_RETENTION_SECONDS', '7776000'))
PRIVILEGED_MAX_ACTIVE_SESSIONS = int(os.getenv('PRIVILEGED_MAX_ACTIVE_SESSIONS', '2'))
ACCOUNT_SESSION_SYNC_SECONDS = int(os.getenv('ACCOUNT_SESSION_SYNC_SECONDS', '300'))
