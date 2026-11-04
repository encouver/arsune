FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
    && .venv/bin/playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*
COPY backend ./backend
COPY alembic.ini ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "arsune.api:app", "--host", "0.0.0.0", "--port", "8540"]
