.PHONY: install install-all install-dev build publish publish-test clean lint format

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

lint:
	ruff check src/
	ruff format --check src/

format:
	ruff check --fix src/
	ruff format src/
