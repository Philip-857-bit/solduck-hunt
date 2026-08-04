FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/solduck.db

WORKDIR /app

RUN useradd --create-home --uid 10001 --user-group \
    --shell /usr/sbin/nologin solduck \
    && mkdir -p /data \
    && chown solduck:solduck /data

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r requirements.txt

COPY --chown=solduck:solduck bot.py config.py db.py game.py messages.py ./

USER solduck
VOLUME ["/data"]
STOPSIGNAL SIGTERM

CMD ["python", "bot.py"]
