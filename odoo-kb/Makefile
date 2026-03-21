.PHONY: help install dev test lint build up down logs demo clean migrate migrate-create migrate-stamp

SHELL := /bin/bash

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Development ──────────────────────────────────────────────

install: ## Install production dependencies
	pip install -e ".[openai,pdf,html]"

dev: ## Install all dependencies (dev + optional)
	pip install -e ".[all,dev]"

test: ## Run test suite
	python -m pytest tests/ -v --tb=short

lint: ## Run linter
	ruff check app/ tests/

# ─── Database Migrations ─────────────────────────────────────

migrate: ## Run database migrations to latest
	alembic upgrade head

migrate-create: ## Create a new migration (usage: make migrate-create msg='add foo')
	alembic revision --autogenerate -m "$(msg)"

migrate-stamp: ## Stamp existing DB as current (usage: make migrate-stamp rev=001)
	alembic stamp $(rev)

# ─── Docker ───────────────────────────────────────────────────

build: ## Build Docker image
	docker compose build

up: ## Start all services (DB + KB service)
	docker compose up -d
	@echo ""
	@echo "KB service: http://localhost:8100"
	@echo "Health:     http://localhost:8100/v1/health"
	@echo "Docs:       http://localhost:8100/docs"
	@echo ""

down: ## Stop all services
	docker compose down

logs: ## Tail service logs
	docker compose logs -f kb-service

# ─── Demo ─────────────────────────────────────────────────────

demo: up demo-seed ## Start services and seed demo data
	@echo ""
	@echo "╔══════════════════════════════════════════════╗"
	@echo "║  KB Service Demo Ready!                      ║"
	@echo "║                                              ║"
	@echo "║  API Docs:   http://localhost:8100/docs      ║"
	@echo "║  Health:     http://localhost:8100/v1/health  ║"
	@echo "║  Analytics:  http://localhost:8100/v1/analytics║"
	@echo "║                                              ║"
	@echo "║  Try:                                        ║"
	@echo "║    make demo-search q='return policy'        ║"
	@echo "║    make demo-search q='how to install'       ║"
	@echo "╚══════════════════════════════════════════════╝"
	@echo ""

demo-seed: ## Seed demo documents into the KB
	@echo "Seeding demo documents..."
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"content": "Our return policy allows returns within 30 days of purchase. Items must be in original condition with receipt. Electronics have a 15-day return window. Refunds are processed within 5-7 business days.", "title": "Return Policy", "tags": ["policy", "returns"], "metadata": {"document_type": "policy"}}' | python -m json.tool
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"content": "To install the Odoo Knowledge Base module: 1) Go to Apps menu 2) Search for Knowledge Base Connector 3) Click Install 4) Configure the KB service URL in Settings > Technical > Knowledge Base. The default URL is http://localhost:8100.", "title": "Installation Guide", "tags": ["guide", "installation"], "metadata": {"document_type": "tutorial"}}' | python -m json.tool
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"content": "Shipping options: Standard (5-7 days, free over $50), Express (2-3 days, $9.99), Next Day ($19.99). International shipping available to 40+ countries. Track your order at orders.example.com.", "title": "Shipping Information", "tags": ["shipping", "delivery"], "metadata": {"document_type": "faq"}}' | python -m json.tool
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"content": "Common errors: E001 Connection timeout - check your network. E002 Authentication failed - verify API key. E003 Rate limit exceeded - wait 60 seconds. E004 Invalid request format - check JSON payload. For persistent issues, contact support@example.com.", "title": "Troubleshooting Guide", "tags": ["troubleshooting", "errors"], "metadata": {"document_type": "troubleshooting"}}' | python -m json.tool
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"content": "Pricing plans: Starter ($29/month, 1000 queries), Business ($99/month, 10000 queries, priority support), Enterprise (custom pricing, unlimited queries, dedicated support, SLA). All plans include 14-day free trial.", "title": "Pricing Plans", "tags": ["pricing", "plans"], "metadata": {"document_type": "product"}}' | python -m json.tool
	@echo "Done! 5 demo documents seeded."

demo-search: ## Search the KB (usage: make demo-search q='your query')
	@curl -s -X POST http://localhost:8100/v1/documents \
		-H "Content-Type: application/json" \
		-d '{"query": "$(q)", "limit": 5}' \
		http://localhost:8100/v1/search | python -m json.tool

demo-voip: ## Test VOIP webhook (usage: make demo-voip q='your query')
	@curl -s -X POST http://localhost:8100/v1/voip/query \
		-H "Content-Type: application/json" \
		-d '{"query": "$(q)", "call_id": "demo-call-001", "provider": "manual", "caller_number": "+1234567890"}' | python -m json.tool

# ─── Cleanup ──────────────────────────────────────────────────

clean: down ## Stop services and remove volumes
	docker compose down -v
	rm -rf .pytest_cache __pycache__ app/__pycache__
