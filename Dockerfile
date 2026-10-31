# ==========================================
# Ultroid-Neo: Lightweight Multi-Stage Dockerfile
# ==========================================
FROM python:3.12-slim

# Install system dependencies (ffmpeg for audio/video splitting, git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for rapid dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy dependency configuration
COPY requirements.txt .

# Install dependencies using uv into system environment
RUN uv pip install --system -r requirements.txt

# Copy project files
COPY . .

# Ensure data directory exists
RUN mkdir -p data

# Default port for PaaS keepalive
EXPOSE 8080

CMD ["python", "main.py"]
