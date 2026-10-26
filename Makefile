.PHONY: help install fmt lint test up down dataset ingest ui eval

help:           ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/'

install:        ## install the package + dev tooling
	pip install -e ".[dev,api,ui]"

fmt:            ## format with black + ruff
	black src tests scripts
	ruff check --fix src tests scripts

lint:           ## lint without modifying
	ruff check src tests scripts

test:           ## run the test suite
	pytest

up:             ## start Postgres + Ollama + the API
	docker compose up -d
	@echo "Pull the embedding model once:  docker compose exec ollama ollama pull bge-m3"

down:           ## stop the stack
	docker compose down

dataset:        ## regenerate the synthetic corpus (needs a Thai font)
	python scripts/gen_dataset.py

ingest:         ## load the corpus: provenance + chunks + SQL knowledge base
	python scripts/ingest.py

ui:             ## run the demo UI on http://localhost:8501 (stack must be up + ingested)
	streamlit run src/vantage/ui/app.py --server.address localhost

eval:           ## run the golden set and write EVAL.md
	python scripts/run_eval.py
