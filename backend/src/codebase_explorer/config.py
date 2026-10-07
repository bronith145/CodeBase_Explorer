"""Typed application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "changeme-please"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "code_chunks"

    llm_provider: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434"

    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768

    max_traversal_depth: int = 3
    max_results: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()

