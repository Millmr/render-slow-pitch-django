# Everyday commands, all run inside Docker. `make help` lists them.
COMPOSE = docker compose
RUN = $(COMPOSE) run --rm -e DJANGO_MIGRATE=0 web

.PHONY: help build up down logs migrations migrate test shell superuser seed psql reset-db prod-image

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-12s %s\n", $$1, $$2}'

build: ## Build the image
	$(COMPOSE) build

up: ## Start Postgres + API on http://localhost:8000
	$(COMPOSE) up --build

down: ## Stop containers
	$(COMPOSE) down

logs: ## Tail API logs
	$(COMPOSE) logs -f web

migrations: ## Create new migration files after model changes
	$(RUN) python manage.py makemigrations

migrate: ## Apply migrations
	$(RUN) python manage.py migrate

test: ## Run the test suite against Postgres
	$(RUN) python manage.py test

shell: ## Django shell
	$(RUN) python manage.py shell

superuser: ## Create an admin user
	$(RUN) python manage.py createsuperuser

seed: ## Load the demo team (manager / softball-demo-1)
	$(COMPOSE) run --rm web python manage.py seed_demo

psql: ## Open psql on the dev database
	$(COMPOSE) exec db psql -U slowpitch -d slowpitch

reset-db: ## Delete the dev database volume
	$(COMPOSE) down -v

prod-image: ## Build the production image and run it locally on :8000
	docker build -t slowpitch-api .
	@echo "Run with: docker run --rm -p 8000:8000 -e DJANGO_SECRET_KEY=... -e DATABASE_URL=... -e DJANGO_ALLOWED_HOSTS=localhost slowpitch-api"
