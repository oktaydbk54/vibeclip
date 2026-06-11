# VibeClip — chat-driven AI short-form video editor
FROM python:3.12-slim

# System deps: ffmpeg (pipeline), fonts for Pillow caption overlay, libglib for opencv-headless
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (mirror of pyproject [project].dependencies) before copying
# the code so this layer is cached across code-only changes.
RUN pip install --no-cache-dir \
        "fastapi>=0.136.3" \
        "faster-whisper>=1.2.1" \
        "mcp[cli]>=1.2.0" \
        "numpy>=2.4.6" \
        "openai>=2.41.0" \
        "opencv-python-headless>=4.13.0.92" \
        "pillow>=12.2.0" \
        "python-dotenv>=1.2.2" \
        "rich>=13" \
        "uvicorn>=0.49.0"

COPY . .

# Bind all interfaces inside the container; the compose port mapping restricts
# host-side exposure to the docker bridge (reachable only by Caddy, not public).
ENV HOST=0.0.0.0 \
    PORT=8765 \
    VIDEO_ENCODER=libx264

EXPOSE 8765

CMD ["python", "-m", "chat.app"]
