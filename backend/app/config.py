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
    
    # To use a hosted provider (e.g. DeepSeek), override these in .env — do NOT
    # hardcode keys here. Example .env:
    #   MODEL_BASE_URL=https://api.deepseek.com
    #   MODEL_NAME=deepseek-chat        # non-thinking; supports tool calling
    #   MODEL_API_KEY=sk-...
    # Note: DeepSeek's API rejects image_url (no vision); use llama-server/Qwen-VL
    # for image attachments. See scripts/check_provider_vision.py.

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
    # --- Vision (§v1.1-V1): per-image blob size cap (10 MB default) ---
    max_image_bytes: int = 10 * 1024 * 1024
    # --- Vision (§v1.1-V2): max total image_url blocks re-fed to the model ---
    max_vision_images_per_turn: int = 6

    # --- OCR (§6, §8.1): PDFOxide / PaddleOCR-v4 ONNX (see 2026-07-21 spec) ---
    ocr_enabled: bool = True  # when False, extract native text only (no OCR)
    # Recognition models to provision (Latin covers the Indonesian corpus).
    ocr_languages: list[str] = ["english", "latin"]
    # Optional override for the PDFOxide model cache (PDF_OXIDE_MODEL_DIR).
    pdf_oxide_model_dir: str | None = None

    # --- Ingest jobs: max concurrent detached ingest tasks (one embedder/Qdrant) ---
    max_concurrent_ingests: int = 2

    # Max concurrent detached chat turns (I/O-bound on the model; generous).
    max_concurrent_chat_turns: int = 16

    # --- Admin bootstrap (§M1) ---
    admin_email: str | None = None
    admin_password: str | None = None

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

    # --- MCP catalog (§M4a): Fernet key for bearer token encryption ---
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    mcp_token_key: str | None = None

    # --- Code-exec sandbox (§13): service URL + per-call timeout ---
    code_exec_url: str = "http://localhost:8001"
    code_exec_timeout_seconds: float = 60

    # --- Voice (§1a): STT sidecar. Empty disables the feature entirely, which
    #     is how the 8 GB dev Mac runs without loading any model.
    voice_service_url: str = ""
    voice_timeout_seconds: float = 120
    max_audio_bytes: int = 10 * 1024 * 1024   # 10 MiB
    # Startup /health probe (spec §5). Short on purpose: a configured-but-dead
    # sidecar must not add two minutes to every backend start.
    voice_probe_timeout_seconds: float = 3

    # --- CORS: prod is same-origin (reverse proxy); this is dev split-origin
    #     convenience only (§2). ---
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- SPA serving (§11 prod): path to the built frontend (e.g. frontend/build/client).
    #     None (default) = disabled; Vite dev-proxy is used instead.
    #     When set AND the dir exists, FastAPI serves the SPA at / and falls back to
    #     index.html for client-side routes (see app/main.py _mount_spa). ---
    spa_dir: str | None = None


settings = Settings()
