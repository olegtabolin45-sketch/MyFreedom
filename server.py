import os
import time
import re
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field, field_validator
import jwt
import bcrypt
from trino.dbapi import connect

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

# --- ТВОИ ОРИГИНАЛЬНЫЕ НАСТРОЙКИ ПОДКЛЮЧЕНИЯ К TRINO ---
def get_trino_connection():
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
async def register_user(data: RegisterRequest):
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    normalized_email = data.email.lower()

    try:
        conn = get_trino_connection()
        cursor = conn.cursor()

        # Проверка дубликата (параметризованный запрос)
        cursor.execute(
            "SELECT email FROM iceberg.demo_db.users WHERE email = ?",
            (normalized_email,)
        )
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Аккаунт с таким Email уже существует."
            )

        hashed_password = hash_password(data.password)

        # INSERT пользователя (параметризованный запрос)
        cursor.execute(
            """
            INSERT INTO iceberg.demo_db.users (created_at, username, email, password_hash, is_onboarded)
            VALUES (CAST(? AS TIMESTAMP), ?, ?, ?, false)
            """,
            (now_str, data.username, normalized_email, hashed_password)
        )
        conn.commit()
        
        token = generate_jwt_token(data.username, normalized_email)
        return {"status": "success", "token": token, "username": data.username, "is_onboarded": False}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"[SECURITY ALERT] Register Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Внутренняя ошибка сервера при регистрации."
        )


@app.post("/api/login")
async def login_user(data: LoginRequest):
    normalized_email = data.email.lower()

    try:
        conn = get_trino_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT username, password_hash, is_onboarded FROM iceberg.demo_db.users WHERE email = ?",
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
        print(f"[SECURITY ALERT] Login Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка авторизации."
        )


@app.get("/api/user/status")
async def get_user_status(token: str):
    email = decode_jwt_token(token)

    try:
        conn = get_trino_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT username, is_onboarded FROM iceberg.demo_db.users WHERE email = ?",
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
        raise HTTPException(status_code=500, detail=f"Ошибка проверки статуса: {str(e)}")


@app.post("/api/onboarding")
async def save_onboarding(data: OnboardingRequest, token: str):
    email = decode_jwt_token(token)
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    try:
        conn = get_trino_connection()
        cursor = conn.cursor()

        # 1. Очистка старых целей (эмуляция REPLACE)
        cursor.execute(
            "DELETE FROM iceberg.demo_db.user_goals WHERE email = ?",
            (email,)
        )

        # 2. Чистый INSERT (параметризованный запрос)
        cursor.execute(
            """
            INSERT INTO iceberg.demo_db.user_goals
            (email, currency, initial_capital, monthly_deposit, target_income, years_horizon, risk_profile, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CAST(? AS TIMESTAMP))
            """,
            (email, data.currency, data.initial_capital, data.monthly_deposit,
             data.target_income, data.years_horizon, data.risk_profile, now_str)
        )

        # 3. Обновление флага в users
        cursor.execute(
            "UPDATE iceberg.demo_db.users SET is_onboarded = true WHERE email = ?",
            (email,)
        )

        conn.commit()
        return {"status": "success", "message": "Данные онбординга успешно сохранены в Trino."}
        
    except Exception as e:
        print(f"[TRINO ERROR] Onboarding Save Fail: {e}")
        raise HTTPException(
            status_code=500, 
            detail="Ошибка записи данных конфигурации целей в Iceberg."
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)