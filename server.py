"""Точка входа. Приложение собрано в пакете app/ (см. app/main.py)."""

from app.main import app

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
