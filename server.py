import os
import time
import re
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
import jwt
import bcrypt  
from trino.dbapi import connect

# --- ОРИГИНАЛЬНЫЕ СИСТЕМНЫЕ НАСТРОЙКИ (БЛОКИРОВКА ПРОКСИ) ---
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""
os.environ["no_proxy"] = "localhost,127.0.0.1"

JWT_SECRET = "SUPER_SECRET_KEY_MY_FREEDOM_2026_PROD" 
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60

app = FastAPI(title="MyFreedom Core API", version="1.0.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ И ЭКРАНИРОВАНИЕ ---
def escape_sql(val: str) -> str:
    """Экранирует одинарные кавычки для безопасной вставки в SQL-запрос Trino"""
    return str(val).replace("'", "''")

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

# --- ЭНДПОИНТЫ API ---

@app.post("/api/register", status_code=status.HTTP_201_CREATED)
async def register_user(data: RegisterRequest):
    now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    normalized_email = data.email.lower()
    
    # Безопасное экранирование строк
    safe_email = escape_sql(normalized_email)
    safe_username = escape_sql(data.username)
    
    try:
        conn = get_trino_connection()
        cursor = conn.cursor()
        
        # Проверка дубликата
        check_query = "SELECT email FROM iceberg.demo_db.users WHERE email = '%s'" % safe_email
        cursor.execute(check_query)
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Аккаунт с таким Email уже существует."
            )
        
        hashed_password = hash_password(data.password)
        safe_password = escape_sql(hashed_password)

        # INSERT пользователя в формате строки (Trino-совместимый)
        insert_query = """
            INSERT INTO iceberg.demo_db.users (created_at, username, email, password_hash, is_onboarded) 
            VALUES (TIMESTAMP '%s', '%s', '%s', '%s', false)
        """ % (now_str, safe_username, safe_email, safe_password)
        
        cursor.execute(insert_query)
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
    safe_email = escape_sql(normalized_email)
    
    try:
        conn = get_trino_connection()
        cursor = conn.cursor()
        
        query = "SELECT username, password_hash, is_onboarded FROM iceberg.demo_db.users WHERE email = '%s'" % safe_email
        cursor.execute(query)
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
    safe_email = escape_sql(email)
    
    try:
        conn = get_trino_connection()
        cursor = conn.cursor()
        
        query = "SELECT username, is_onboarded FROM iceberg.demo_db.users WHERE email = '%s'" % safe_email
        cursor.execute(query)
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
    
    safe_email = escape_sql(email)
    safe_currency = escape_sql(data.currency)
    safe_risk = escape_sql(data.risk_profile)
    
    try:
        conn = get_trino_connection()
        cursor = conn.cursor()
        
        # 1. Очистка старых целей (эмуляция REPLACE)
        delete_goals = "DELETE FROM iceberg.demo_db.user_goals WHERE email = '%s'" % safe_email
        cursor.execute(delete_goals)
        
        # 2. Чистый INSERT
        insert_goals = """
            INSERT INTO iceberg.demo_db.user_goals 
            (email, currency, initial_capital, monthly_deposit, target_income, years_horizon, risk_profile, updated_at)
            VALUES ('%s', '%s', %f, %f, %f, %d, '%s', TIMESTAMP '%s')
        """ % (safe_email, safe_currency, data.initial_capital, data.monthly_deposit, 
               data.target_income, data.years_horizon, safe_risk, now_str)
        cursor.execute(insert_goals)
        
        # 3. Обновление флага в users
        update_user = "UPDATE iceberg.demo_db.users SET is_onboarded = true WHERE email = '%s'" % safe_email
        cursor.execute(update_user)
        
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