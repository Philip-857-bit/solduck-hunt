FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/app/data/solduck.db

WORKDIR /app

RUN useradd --create-home --uid 10001 --user-group \
    --shell /usr/sbin/nologin solduck \
    && mkdir -p /app/data \
    && chown solduck:solduck /app \
    && chown solduck:solduck /app/data

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r requirements.txt

COPY --chown=solduck:solduck \
    bot.py config.py db.py postgres_store.py game.py messages.py ./
COPY --chown=solduck:solduck assets ./assets

USER solduck
EXPOSE 8080
STOPSIGNAL SIGTERM

CMD ["python", "bot.py"]
