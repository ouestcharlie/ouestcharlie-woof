.PHONY: pack build-gallery test-gallery test-python test-python-int smoke

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

# Starts a real `python -m woof` process, checks the discovery file + an
# authenticated /healthz, then stops it via the real production shutdown
# path (POST /shutdown, same as woof-bridge/idle-timeout use) and verifies
# clean shutdown (process exits, discovery file removed, no errors logged).
#
# Note: raw SIGTERM is deliberately NOT used here. uvicorn's Server.capture_signals()
# restores the *default* signal disposition before re-raising the captured signal,
# which kills the process immediately and skips our own asyncio.gather(...)'s
# `finally` block (remove_discovery/agent.shutdown never run) — a pre-existing
# uvicorn behavior, not something this target is meant to cover.
#
# Known slow path: __main__.py's watch_idle() coroutine sleeps in
# _IDLE_CHECK_INTERVAL_SECONDS chunks and only re-checks should_exit() after
# each sleep completes, so asyncio.gather(...)'s `finally` (remove_discovery,
# agent.shutdown) can be delayed up to that interval after /shutdown — a
# pre-existing gap in watch_idle()'s shutdown responsiveness (discovery.py),
# not something this target works around beyond tolerating it with a longer poll.
smoke:
	@set -e; \
	DISC="$$HOME/Library/Application Support/ouestcharlie/woof-discovery.json"; \
	LOG=$$(mktemp); \
	rm -f "$$DISC"; \
	.venv/bin/python -m woof > "$$LOG" 2>&1 & \
	PID=$$!; \
	for i in $$(seq 1 50); do [ -f "$$DISC" ] && break; sleep 0.1; done; \
	if [ ! -f "$$DISC" ]; then echo "FAIL: discovery file never appeared"; cat "$$LOG"; kill -9 $$PID 2>/dev/null; exit 1; fi; \
	TOKEN=$$(python3 -c "import json;print(json.load(open('$$DISC'))['token'])"); \
	PORT=$$(python3 -c "import json;print(json.load(open('$$DISC'))['port'])"); \
	HEALTH=$$(curl -s -H "Authorization: Bearer $$TOKEN" "http://127.0.0.1:$$PORT/healthz"); \
	echo "healthz: $$HEALTH"; \
	echo "$$HEALTH" | grep -q '"status":"ok"' || { echo "FAIL: healthz did not return ok"; cat "$$LOG"; kill -9 $$PID 2>/dev/null; exit 1; }; \
	curl -s -X POST -H "Authorization: Bearer $$TOKEN" "http://127.0.0.1:$$PORT/shutdown"; echo; \
	for i in $$(seq 1 350); do kill -0 $$PID 2>/dev/null || break; sleep 0.1; done; \
	if kill -0 $$PID 2>/dev/null; then echo "FAIL: process still running 35s after /shutdown"; kill -9 $$PID; cat "$$LOG"; exit 1; fi; \
	if [ -f "$$DISC" ]; then echo "FAIL: discovery file not removed on shutdown"; cat "$$LOG"; exit 1; fi; \
	if grep -qi "error\|traceback" "$$LOG"; then echo "FAIL: errors in log"; cat "$$LOG"; exit 1; fi; \
	echo "OK: smoke test passed"
