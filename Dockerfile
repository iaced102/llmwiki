FROM python:3.12-slim AS builder

WORKDIR /app

# System deps: build tools + Playwright/Chromium runtime libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    # Playwright / Chromium runtime deps
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxext6 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip --no-cache-dir

# Install dependencies in a separate layer so they're cached on code-only changes
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Install Playwright browsers (Chromium only) for crawl4ai
RUN crawl4ai-setup || python -m playwright install --with-deps chromium

# Copy application source and migration files
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY skills/ ./skills/

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5055"]
