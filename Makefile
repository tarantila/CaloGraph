.PHONY: dev up down logs migrate test lint typecheck frontend-test e2e build backup backup-secrets update

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

migrate:
	docker compose run --rm backend alembic upgrade head

test:
	docker compose --profile test run --rm --build backend-ci pytest
	./scripts/test-postgres.sh

lint:
	docker compose --profile test run --rm --build backend-ci ruff check app tests
	docker compose run --rm frontend-ci npm run lint

typecheck:
	docker compose --profile test run --rm --build backend-ci mypy app
	docker compose run --rm frontend-ci npm run typecheck

frontend-test:
	docker compose run --rm frontend-ci npm run test:unit

e2e:
	./scripts/e2e.sh

build:
	docker compose build

backup:
	./scripts/backup-postgres.sh

backup-secrets:
	./scripts/backup-secrets.sh

update:
	./scripts/update-containers.sh
