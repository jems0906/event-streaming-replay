GETTING_STARTED:
	@echo "Starting event streaming platform..."
	docker compose up --build -d
	@echo "Waiting for services to be ready (30s)..."
	sleep 30
	@echo "Platform is up. Run 'make check' to validate."

UP:
	docker compose up --build -d

DOWN:
	docker compose down

LOGS:
	docker compose logs -f

HEALTH:
	@echo "Checking service health..."
	curl -s http://localhost:8000/health | jq '.' || echo "Gateway not ready"
	curl -s http://localhost:8001/health | jq '.' || echo "Core not ready"
	curl -s http://localhost:8002/health | jq '.' || echo "Replay not ready"

SMOKE_TEST:
	python tests/smoke_tests.py

INTEGRATION_TEST:
	python tests/integration_helpers.py

LOAD_TEST:
	python tests/load_test.py --duration 30 --rps 10

GRAFANA:
	@echo "Opening Grafana dashboard..."
	start http://localhost:3000

PROMETHEUS:
	@echo "Opening Prometheus..."
	start http://localhost:9090

JAEGER:
	@echo "Opening Jaeger traces..."
	start http://localhost:16686

CHECK: HEALTH SMOKE_TEST

LIVE_TRAFFIC:
	@echo "Sending 5 live traffic requests..."
	for i in 1 2 3 4 5; do \
		curl -s -X POST http://localhost:8000/ingest \
			-H "Content-Type: application/json" \
			-d "{\"user_id\":$$i,\"action\":\"test_$$i\"}" | jq '.'; \
		sleep 0.5; \
	done

REPLAY_10x:
	@echo "Triggering 10x speedup replay..."
	curl -s -X POST http://localhost:8002/replay \
		-H "Content-Type: application/json" \
		-d '{"environment":"dev","speedup_factor":10,"max_events":50,"target_url":"http://gateway:8000/ingest"}' | jq '.'

CLEAN:
	docker compose down -v
	@echo "Cleaned up all containers and volumes."

.PHONY: UP DOWN LOGS HEALTH SMOKE_TEST INTEGRATION_TEST LOAD_TEST GRAFANA PROMETHEUS JAEGER CHECK LIVE_TRAFFIC REPLAY_10x CLEAN GETTING_STARTED
