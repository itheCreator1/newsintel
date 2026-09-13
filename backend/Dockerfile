FROM python:3.13-slim AS runtime
RUN groupadd --system --gid 10001 newsintel && useradd --system --uid 10001 --gid newsintel newsintel
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN mkdir -p /var/lib/newsintel/articles && chown -R newsintel:newsintel /var/lib/newsintel
USER newsintel
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
