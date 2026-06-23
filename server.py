import os
import time
import re
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, status, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
import jwt
import bcrypt
import pg8000.dbapi

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --- ОРИГИНАЛЬНЫЕ СИСТЕМНЫЕ НАСТРОЙКИ (БЛОКИРОВКА ПРОКСИ) ---
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["no_proxy"] = "localhost,127.0.0.1"

# --- СЕКРЕТЫ И КОНФИГУРАЦИЯ (из переменных окружения) ---
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError(
        "Переменная окружения JWT_SECRET не задана. "
        "Создайте файл .env (см. .env.example) с надёжным секретным ключом."
    )
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = int(os.environ.get("TOKEN_EXPIRE_MINUTES", "60"))

# --- ЛОГИРОВАНИЕ (без утечки чувствительных данных клиенту) ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aeterna")

# Разрешённые источники для CORS (через запятую в ALLOWED_ORIGINS)
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
    if origin.strip()
]

app = FastAPI(title="MyFreedom Core API", version="1.0.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- СХЕМЫ ВАЛИДАЦИИ ДАННЫХ (Pydantic) ---
class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    email: EmailStr
    password: str

    @field_validator('password')
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Пароль должен быть не менее 8 символов.')
        if not re.search(r"[A-ZА-Я]", v):
            raise ValueError('Пароль должен содержать хотя бы одну заглавную букву.')
        if not re.search(r"[a-zа-я]", v):
            raise ValueError('Пароль должен содержать хотя бы одну строчную букву.')
        if not re.search(r"[0-9]", v):
            raise ValueError('Пароль должен содержать хотя бы одну цифру.')
        if not re.search(r"[@$!%*?&_#№\-+=/\\|()\[\]{}]", v):
            raise ValueError('Пароль должен содержать хотя бы один специальный символ (например, @, $, !, %).')
        return v

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class OnboardingRequest(BaseModel):
    currency: str
    initial_capital: float = Field(..., gt=0)
    monthly_deposit: float = Field(..., ge=0)
    target_income: float = Field(..., gt=0)
    years_horizon: int = Field(..., gt=0)
    risk_profile: str

# --- ПОДКЛЮЧЕНИЕ К POSTGRESQL (пользователи, сессии, цели) ---
def get_db_connection():
    return pg8000.dbapi.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "5433")),
        database=os.environ.get("DB_NAME", "aeterna"),
        user=os.environ.get("DB_USER", "aeterna"),
        password=os.environ.get("DB_PASSWORD", ""),
    )

# --- ПОДКЛЮЧЕНИЕ К TRINO (зарезервировано для аналитики капитала, см. ROADMAP, этап 4) ---
def get_trino_connection():
    from trino.dbapi import connect
    return connect(
        host="127.0.0.1", port=8080, user="admin", catalog="iceberg", schema="demo_db"
    )

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def hash_password(password: str) -> str:
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))
    except Exception:
        return False

def generate_jwt_token(username: str, email: str) -> str:
    payload = {
        "sub": email,
        "name": username,
        "exp": time.time() + (TOKEN_EXPIRE_MINUTES * 60)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_jwt_token(token: str) -> str:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload["sub"]  # Возвращает email
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Срок действия токена истек.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Невалидный токен сессии.")

# --- RATE LIMITING (в памяти, скользящее окно) ---
# Примечание: лимитер хранит состояние в памяти процесса. Для нескольких
# инстансов нужен общий бэкенд (Redis) — см. ROADMAP, этап 2.
_rate_buckets = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(request: Request, scope: str, max_requests: int, window_seconds: int):
    """Ограничивает число запросов с одного IP. Бросает 429 при превышении."""
    client_ip = request.client.host if request.client else "unknown"
    key = f"{scope}:{client_ip}"
    now = time.time()
    cutoff = now - window_seconds
    with _rate_lock:
        timestamps = _rate_buckets[key]
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= max_requests:
            retry_after = int(window_seconds - (now - timestamps[0])) + 1
            logger.warning("Rate limit превышен: scope=%s ip=%s", scope, client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Слишком много попыток. Пожалуйста, попробуйте позже.",
                headers={"Retry-After": str(retry_after)}
            )
        timestamps.append(now)

# --- ОТДАЧА ФРОНТЕНДА (тот же origin, что и API — чтобы не ослаблять CORS) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))

@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse(os.path.join(BASE_DIR, "dashboard.html"))

# --- ЭНДПОИНТЫ API ---

@app.post("/api/register", status_code=status.HTTP_201_CREATED)
async def register_user(data: RegisterRequest, request: Request):
    check_rate_limit(request, "register", max_requests=5, window_seconds=60)
    normalized_email = data.email.lower()

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Проверка дубликата (параметризованный запрос)
        cursor.execute(
            "SELECT email FROM users WHERE email = %s",
            (normalized_email,)
        )
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Аккаунт с таким Email уже существует."
            )

        hashed_password = hash_password(data.password)

        # INSERT пользователя (created_at проставляется БД по умолчанию)
        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, is_onboarded)
            VALUES (%s, %s, %s, false)
            """,
            (data.username, normalized_email, hashed_password)
        )
        conn.commit()

        token = generate_jwt_token(data.username, normalized_email)
        return {"status": "success", "token": token, "username": data.username, "is_onboarded": False}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка регистрации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при регистрации."
        )
    finally:
        if conn is not None:
            conn.close()


@app.post("/api/login")
async def login_user(data: LoginRequest, request: Request):
    check_rate_limit(request, "login", max_requests=10, window_seconds=60)
    normalized_email = data.email.lower()

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT username, password_hash, is_onboarded FROM users WHERE email = %s",
            (normalized_email,)
        )
        user = cursor.fetchone()

        if not user:
            fake_salt = b'$2b$12$L7RMD8clNRE1bepshLrrUu'
            bcrypt.hashpw(data.password.encode('utf-8'), fake_salt)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль."
            )

        username, db_hashed_password, is_onboarded = user[0], user[1], user[2]

        if not verify_password(data.password, db_hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Неверный адрес электронной почты или пароль."
            )

        token = generate_jwt_token(username, normalized_email)
        return {
            "status": "success",
            "token": token,
            "username": username,
            "is_onboarded": bool(is_onboarded)
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка авторизации: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка авторизации."
        )
    finally:
        if conn is not None:
            conn.close()


@app.get("/api/user/status")
async def get_user_status(token: str):
    email = decode_jwt_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT username, is_onboarded FROM users WHERE email = %s",
            (email,)
        )
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден.")

        return {
            "username": user[0],
            "is_onboarded": bool(user[1])
        }
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка проверки статуса: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Ошибка проверки статуса.")
    finally:
        if conn is not None:
            conn.close()


@app.post("/api/onboarding")
async def save_onboarding(data: OnboardingRequest, token: str):
    email = decode_jwt_token(token)

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Очистка старых целей (эмуляция REPLACE)
        cursor.execute(
            "DELETE FROM user_goals WHERE email = %s",
            (email,)
        )

        # 2. Чистый INSERT (updated_at проставляется БД по умолчанию)
        cursor.execute(
            """
            INSERT INTO user_goals
            (email, currency, initial_capital, monthly_deposit, target_income, years_horizon, risk_profile)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (email, data.currency, data.initial_capital, data.monthly_deposit,
             data.target_income, data.years_horizon, data.risk_profile)
        )

        # 3. Обновление флага в users
        cursor.execute(
            "UPDATE users SET is_onboarded = true WHERE email = %s",
            (email,)
        )

        conn.commit()
        return {"status": "success", "message": "Данные онбординга успешно сохранены."}

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Ошибка сохранения онбординга: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Ошибка записи данных конфигурации целей."
        )
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
