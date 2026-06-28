"""Application settings — every knob the spec calls out, in one place.

Values come from the environment / a local `.env` (see `.env.example`).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- App data store (§9.1): SQLite now, Postgres/MySQL later by URL ---
    database_url: str = "sqlite:///./data/app.db"

    # --- Vector store (§9.2) ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "kb_chunks"

    # --- Blob storage (§8.1): local dir for raw uploaded files ---
    blob_dir: str = "./data/blobs"

    # --- Chat model: any OpenAI-compatible endpoint (§7, §12) ---
    model_base_url: str = "https://llama.sccic.org/v1"
    model_name: str = "qwen"
    model_api_key: str = "not-needed"  # llama-server ignores it; set for hosted APIs

    # --- Embeddings / rerank: FlagEmbedding (BGE-M3), in-process (§8.1, §8.5) ---
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_enabled: bool = False  # recall-only ships first (§8.5)
    # Device is auto-detected (cuda → mps → cpu); override here if needed.
    # Portable across Windows+CUDA, Mac M1 (mps), and CPU.
    embed_device: str = "auto"  # auto | cuda | mps | cpu
    embed_use_fp16: bool | None = None  # None = auto (fp16 only on cuda)

    # --- Ingress routing (§6) ---
    inline_token_budget: int = 6000          # tokens, not bytes — the real gate
    max_upload_bytes: int = 100 * 1024 * 1024  # sanity ceiling (§11)

    # --- OCR (§6, §8.1): Surya, in-process, only for thin/absent text layers ---
    ocr_enabled: bool = True
    ocr_min_chars_per_page: int = 100  # below this average, treat the PDF as scanned

    # --- Auth (§4, §10) ---
    jwt_secret: str = "dev-secret-change-me-please-32-bytes-minimum"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    cookie_secure: bool = True  # set False for plain-HTTP dev / tests

    # --- Tool loop safety rails (§7) ---
    max_tool_iterations: int = 6
    max_parallel_tools: int = 2

    # --- MCP (§12.2): path to the YAML server registry ---
    mcp_config_path: str = "./mcp.yaml"
    # Per-call MCP tool timeout. Generous on purpose: real MCP sources (e.g.
    # satudata fetches/scrapes) routinely take 60-90s; the spec (§7) calls these
    # chains slow by nature. Raise further per deployment if a source is slower.
    mcp_tool_timeout_seconds: float = 120

    # --- Code-exec sandbox (§13): service URL + per-call timeout ---
    code_exec_url: str = "http://localhost:8001"
    code_exec_timeout_seconds: float = 60

    # --- CORS: prod is same-origin (reverse proxy); this is dev split-origin
    #     convenience only (§2). ---
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
