# syntax=docker/dockerfile:1

FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

COPY backend/ ./backend/
COPY src/ ./src/
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
