FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
# Скрипты прогона по чек-листам кейса должны быть доступны внутри контейнера:
# на сервере часто нет ни venv, ни зависимостей, а проверить агента надо.
COPY scripts ./scripts
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
