# TraceLens API.
#
# Two stages: dependencies are installed once into a virtualenv, then copied
# into a slim runtime image. That keeps build tooling out of the image that
# ships and makes a code-only change reuse the dependency layer.
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only what the install needs first, so editing source does not
# reinstall every dependency.
COPY pyproject.toml README.md ./
COPY sdk/tracelens/__init__.py sdk/tracelens/__init__.py
COPY backend/app/__init__.py backend/app/__init__.py
RUN pip install -e ".[backend]" psycopg[binary]

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# curl is here for the container health check, not for the application.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Never run the API as root.
RUN useradd --create-home --uid 10001 tracelens

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY --chown=tracelens:tracelens . .

USER tracelens
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
