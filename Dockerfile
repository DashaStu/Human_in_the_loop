FROM python:3.12-slim

WORKDIR /app

# Отключаем лишние .pyc файлы и буферизацию, чтобы логи сразу были видны в консоли
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Сначала ставим библиотеки (Docker запомнит этот слой, и пересборка будет быстрой)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем сам код
COPY app/ ./app/
COPY bot.py web.py ./

RUN mkdir -p /app/data
