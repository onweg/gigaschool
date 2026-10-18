.PHONY: dev api front test lint up down env deploy redeploy logs stop

api:        ## backend на 8000 с офлайн-провайдером
	cd backend && LLM_PROVIDER=mock python3 -m uvicorn app.main:app --reload --port 8000

front:      ## frontend на 5173 с прокси на 8000
	cd frontend && npm run dev

test:
	cd backend && python3 -m pytest tests -q

up:
	docker compose up --build

down:
	docker compose down

# --------------------------------------------------------------------------- #
# Запуск без docker compose: на выданной ВМ стоит docker-compose 1.29, который
# не работает с новым requests. Те же два образа поднимаются напрямую.
# --------------------------------------------------------------------------- #
PORT ?= 8000
#: Файл с доступами, который выдаёт площадка.
PLATFORM_ENV ?= /opt/hackathon/ai.env
#: Имя модели из каталога площадки — в ai.env его нет, задаём сами.
MODEL ?= GigaChat/GigaChat-3-Pro
#: Рабочий файл окружения контейнера; собирается целью `env`.
ENV_FILE ?= .env
#: Сколько запросов к модели одновременно. Шлюз площадки держит 2.
LLM_MAX_PARALLEL ?= 2
#: Снимает ограничение на адрес модели. На площадке не нужен: её шлюз
#: в списке разрешённых. Нужен только при работе на другом провайдере.
LLM_ALLOW_ANY_HOST ?= 0
#: 0 по умолчанию: на защите агент обязан генерировать по-настоящему.
#: 1 — повторные одинаковые запросы берутся с диска и не тратят бюджет,
#: это режим отладки: `make deploy LLM_CACHE=1`.
LLM_CACHE ?= 0

env:        ## собрать .env из доступов площадки
	@test -f $(PLATFORM_ENV) || { echo "нет $(PLATFORM_ENV)"; exit 1; }
	@# docker --env-file не снимает кавычки: ключ "AQVN..." уедет вместе с ними
	@# и авторизация развалится, поэтому чистим их здесь.
	@sed 's/"//g' $(PLATFORM_ENV) > $(ENV_FILE)
	@grep -q '^OPENAI_MODEL=' $(ENV_FILE) || echo "OPENAI_MODEL=$(MODEL)" >> $(ENV_FILE)
	@# Телеметрия закрыта токеном; если его нет — генерируем и показываем один раз.
	@grep -q '^METRICS_TOKEN=' $(ENV_FILE) || \
		echo "METRICS_TOKEN=$$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')" >> $(ENV_FILE)
	@echo "статистика:  #/stats?token=$$(grep '^METRICS_TOKEN=' $(ENV_FILE) | cut -d= -f2-)"
	@chmod 600 $(ENV_FILE)
	@echo "$(ENV_FILE) собран, модель: $$(grep '^OPENAI_MODEL=' $(ENV_FILE) | cut -d= -f2-)"

deploy: env   ## собрать и поднять оба контейнера на $(PORT)
	docker network create acr 2>/dev/null || true
	docker build -t acr-backend ./backend
	docker build -t acr-frontend ./frontend
	docker rm -f backend frontend 2>/dev/null || true
	docker run -d --name backend --network acr --restart unless-stopped \
		$$(test -f $(ENV_FILE) && echo --env-file $(ENV_FILE)) \
		-e DATA_DIR=/app/data -e LLM_MAX_PARALLEL=$(LLM_MAX_PARALLEL) -e LLM_CACHE=$(LLM_CACHE) \
		-e LLM_ALLOW_ANY_HOST=$(LLM_ALLOW_ANY_HOST) \
		-v $(PWD)/backend/data:/app/data acr-backend
	docker run -d --name frontend --network acr --restart unless-stopped \
		-p $(PORT):80 acr-frontend
	@sleep 3 && docker exec backend python -c "import urllib.request;\
print(urllib.request.urlopen('http://localhost:8000/api/health').read().decode())" || true

redeploy: deploy   ## пересборка после обновления кода

logs:       ## логи бэкенда
	docker logs -f backend

stop:
	docker rm -f backend frontend 2>/dev/null || true
