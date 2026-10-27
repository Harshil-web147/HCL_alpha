.PHONY: install run demo test lint docker

install:        ## install dependencies
	pip install -r requirements.txt

run:            ## start API + dashboard on :8000
	uvicorn app.main:app --reload --port 8000

demo:           ## run the reference feature end-to-end (auto-approving every checkpoint)
	python -m app.cli run --example --auto-approve

test:           ## platform self-tests
	pytest -q

lint:           ## lint + security scan of the platform itself
	ruff check . && bandit -q -r app -x app/llm/stub_assets -ll

docker:         ## build and start with docker compose
	docker compose up --build
