FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.14
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY apps/api apps/api
COPY configs configs
RUN useradd --create-home app && mkdir -p /app/staticfiles /app/.local/originals && chown -R app:app /app /opt/venv
USER app
ENV PYTHONPATH=/app/apps/api
CMD ["gunicorn", "config.wsgi:application", "--chdir", "/app/apps/api", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "330", "--access-logfile", "-"]
