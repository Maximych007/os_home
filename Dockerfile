FROM python:3.12-slim

WORKDIR /app

# Для psutil/bcrypt/docker иногда требуются системные зависимости при установке.
# На большинстве платформ с wheel-ами может и не понадобиться, но так надёжнее.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# FastAPI CLI рекомендован для контейнеров (использует Uvicorn внутри) [web:275]
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
