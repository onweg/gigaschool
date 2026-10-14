.PHONY: dev api front test lint up down

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
