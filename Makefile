.PHONY: pack build-gallery test-gallery test-python test-python-int

build-gallery:
	cd gallery && npm run build

test-gallery:
	cd gallery && npm test

test-python:
	.venv/bin/python -m pytest tests/ -v

test-python-int:
	.venv/bin/python -m pytest tests_integration/ -v

pack:
	mcpb pack . dist/ouestcharlie-woof-$(shell grep '^version' pyproject_packaging.toml | sed 's/.*= *"\(.*\)"/\1/').mcpb
