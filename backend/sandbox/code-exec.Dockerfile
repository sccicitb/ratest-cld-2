# Code-exec SERVICE image — runs the sandbox controller (sandbox.service.main).
#
# Build context: backend/   (repo root → backend/)
#   docker build -t rag-code-exec -f sandbox/code-exec.Dockerfile .
#
# This is NOT the runner image (rag-sandbox).
# The runner image is built separately:
#   docker build -t rag-sandbox sandbox/runner
#
# The service mounts /var/run/docker.sock so it can spawn and destroy runner
# containers on the same Docker daemon.

FROM python:3.12-slim

WORKDIR /app

# Install service dependencies (keep in sync with pyproject.toml [sandbox] extra).
RUN pip install --no-cache-dir \
    "fastapi>=0.115" \
    "uvicorn[standard]>=0.30" \
    "docker>=7" \
    "httpx>=0.27" \
    "pydantic-settings>=2.4"

# Copy the sandbox package (service + __init__; runner is image-only, not needed here).
COPY sandbox/__init__.py ./sandbox/__init__.py
COPY sandbox/service/ ./sandbox/service/

EXPOSE 8001

CMD ["uvicorn", "sandbox.service.main:app", "--host", "0.0.0.0", "--port", "8001"]
