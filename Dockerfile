# ==========================================
# NovaCore: High-Performance Async Microservice Dockerfile
# ==========================================
FROM python:3.12-slim

# Install system dependencies (ffmpeg for audio/video processing, git, curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user with UID 1000 for Hugging Face Spaces compatibility
RUN useradd -m -u 1000 user

# Install uv for rapid dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /home/user/app

# Copy dependency configuration
COPY requirements.txt .

# Install dependencies into system environment
RUN uv pip install --system -r requirements.txt

# Copy project files and set ownership
COPY --chown=user:user . .

# Ensure data directory exists and has proper permissions
RUN mkdir -p /home/user/app/data && chown -R user:user /home/user/app/data

# Switch to non-root user
USER user

# Set environment
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860

# Default port for Hugging Face Spaces
EXPOSE 7860

CMD ["python", "main.py"]
