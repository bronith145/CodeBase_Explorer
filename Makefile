up-cpu:
	docker compose --profile cpu up -d

up-gpu:
	docker compose --profile gpu up -d

down:
	docker compose --profile cpu --profile gpu down

pull-models:
	docker exec cbx-ollama ollama pull qwen2.5:7b
	docker exec cbx-ollama ollama pull nomic-embed-text

