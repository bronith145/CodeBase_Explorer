"""FastAPI entrypoint. Endpoints are added in later sections."""

from fastapi import FastAPI

from codebase_explorer import __version__

app = FastAPI(title="Codebase Explorer", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}

