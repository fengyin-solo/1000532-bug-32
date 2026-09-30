.PHONY: install backend frontend test

install:
	cd backend && python3 -m pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

test:
	cd backend && python3 -m pytest tests/ -q
	cd frontend && npm run typecheck
