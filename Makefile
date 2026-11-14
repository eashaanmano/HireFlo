.PHONY: up seed reseed backend frontend test lint neon

up:            ## Run everything with Docker (http://localhost:8080)
	docker compose up --build

reseed:        ## Fresh demo data with a drive that is live right now
	docker compose exec backend python -m app.seed --reset

backend:       ## Local API on :8000 (run inside backend/.venv)
	cd backend && python -m app.seed && uvicorn app.main:app --reload --port 8000

frontend:      ## Local UI on :5173
	cd frontend && npm run dev

neon:          ## Copy local SQLite data to Neon: make neon URL="postgresql://...neon.tech/neondb?sslmode=require"
	cd backend && python -m app.migrate_db --to "$(URL)"

seed:
	cd backend && python -m app.seed --reset

test:
	cd backend && pytest
	cd frontend && npm test

lint:
	cd backend && ruff check app tests
	cd frontend && npm run typecheck
