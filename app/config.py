"""Централизованная конфигурация приложения из переменных окружения."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- Системные настройки (блокировка прокси) ---
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["no_proxy"] = "localhost,127.0.0.1"

# --- Секреты и JWT ---
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "Переменная окружения JWT_SECRET не задана. "
        "Создайте файл .env (см. .env.example) с надёжным секретным ключом."
    )
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = int(os.environ.get("TOKEN_EXPIRE_MINUTES", "60"))

# --- CORS ---
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000"
    ).split(",")
    if origin.strip()
]

# --- PostgreSQL (пользователи, цели) ---
DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("DB_PORT", "5433"))
DB_NAME = os.environ.get("DB_NAME", "aeterna")
DB_USER = os.environ.get("DB_USER", "aeterna")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

# --- Trino (зарезервировано для аналитики капитала, см. ROADMAP, этап 4) ---
TRINO_HOST = os.environ.get("TRINO_HOST", "127.0.0.1")
TRINO_PORT = int(os.environ.get("TRINO_PORT", "8080"))
TRINO_USER = os.environ.get("TRINO_USER", "admin")
TRINO_CATALOG = os.environ.get("TRINO_CATALOG", "iceberg")
TRINO_SCHEMA = os.environ.get("TRINO_SCHEMA", "demo_db")

# Корень проекта (для отдачи статических файлов фронтенда)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
