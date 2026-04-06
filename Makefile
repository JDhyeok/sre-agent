.PHONY: install install-dev build publish publish-test clean

install:
	pip install .

install-all:
	pip install ".[all]"

install-dev:
	pip install -e ".[all,dev]"

build:
	python -m build

publish-test: build
	twine upload --repository testpypi dist/*

publish: build
	twine upload dist/*

clean:
	rm -rf dist/ build/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

check:
	sre-agent check

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

format:
	ruff check --fix src/ tests/
	ruff format src/ tests/

test:
	pytest tests/ -v
