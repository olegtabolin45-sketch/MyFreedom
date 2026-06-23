FROM python:3.12-slim

WORKDIR /app

# Зависимости отдельным слоем для кэширования
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код приложения
COPY . .

EXPOSE 8000

# В контейнере слушаем 0.0.0.0 (а не 127.0.0.1), чтобы порт был доступен снаружи
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
