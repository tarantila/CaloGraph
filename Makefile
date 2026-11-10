.PHONY: dev up down logs migrate analysis-review-up analysis-review-down test lint typecheck frontend-test e2e build backup backup-secrets backup-test update update-test
TEST_PROJECT ?= calograph-tests
TEST_COMPOSE := ENVIRONMENT=test CALOGRAPH_PUBLIC_URL=http://localhost:8180 COOKIE_SECURE=false ENABLE_HSTS=false TRUSTED_HOSTS=localhost,127.0.0.1 TRUSTED_ORIGINS=http://localhost:8180,http://127.0.0.1:8180 docker compose --env-file /dev/null -p $(TEST_PROJECT) -f docker-compose-dev.yml --profile test

dev:
	docker compose -f docker-compose-dev.yml up -d --build

up:
	docker compose -f docker-compose.yml up -d --build

down:
	docker compose -f docker-compose.yml down

logs:
	docker compose -f docker-compose.yml logs -f --tail=200

migrate:
	docker compose -f docker-compose.yml run --rm backend alembic upgrade head

analysis-review-up:
	sh ./scripts/analysis-review.sh up

analysis-review-down:
	sh ./scripts/analysis-review.sh down

test:
	$(TEST_COMPOSE) run --rm --build backend-ci pytest
	./scripts/test-postgres.sh

lint:
	$(TEST_COMPOSE) run --rm --build backend-ci ruff check app tests
	$(TEST_COMPOSE) run --rm frontend-ci npm run lint

typecheck:
	$(TEST_COMPOSE) run --rm --build backend-ci mypy app
	$(TEST_COMPOSE) run --rm frontend-ci npm run typecheck

frontend-test:
	$(TEST_COMPOSE) run --rm frontend-ci npm run test:unit

e2e:
	./scripts/e2e.sh

build:
	docker compose -f docker-compose.yml build

backup:
	./scripts/backup-postgres.sh

backup-secrets:
	./scripts/backup-secrets.sh

backup-test:
	./scripts/test-backup-agent.sh

update:
	./scripts/update-containers.sh
update-test:
	./scripts/test-update-containers.sh
