# Production image: gunicorn + whitenoise. docker-compose.yml reuses it for
# local development with the source mounted and the Django dev server.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x docker/entrypoint.sh \
    && DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && chown -R app /app
USER app

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
# PORT and WEB_CONCURRENCY are provided by hosts such as Render.
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --access-logfile -"]
