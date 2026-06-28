FROM python:3.12-slim

# Non-interactive matplotlib backend — must be set before any matplotlib import.
ENV MPLBACKEND=Agg

# Non-root user uid=1000 (Wall: privilege)
RUN useradd --uid 1000 --create-home sandbox

# /app holds the runner code (part of the read-only root FS).
# /work is the tmpfs scratch dir mounted at runtime (Wall: filesystem).
# These must be separate paths so the tmpfs doesn't hide the runner module.
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py .

# /work and /tmp must exist in the image so Docker can mount tmpfs over them.
# /tmp is writable so IPython and other tools can create temporary files.
RUN mkdir /work && chown sandbox:sandbox /work && chmod 1777 /tmp

USER sandbox

CMD ["uvicorn", "runner:app", "--host", "0.0.0.0", "--port", "8000"]
