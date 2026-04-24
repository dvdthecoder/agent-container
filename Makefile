.PHONY: test lint mcp dashboard

test:
	python3 -m pytest tests/unit/ -v

lint:
	python3 -m ruff check .

mcp:
	python3 -m mcp_server.server --transport stdio

dashboard:
	python3 -m uvicorn dashboard.app:app --reload --port 8000
