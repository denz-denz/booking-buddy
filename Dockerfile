FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
    "anthropic==1.11.0" "python-telegram-bot==22.8" "pydantic==2.13.5" "pydantic-settings==2.15.0" \
    "google-api-python-client==2.201.0" "google-auth==2.59.1" "google-auth-oauthlib==1.5.0" \
    "python-dateutil==2.9.0.post0" "tzdata==2026.5"

COPY src ./src
COPY skills ./skills

RUN useradd --create-home --uid 1000 calbot && mkdir -p /app/data && chown calbot /app/data
USER calbot

# Mount a persistent volume here: SQLite, google_token.json, roster.json
VOLUME ["/app/data"]
CMD ["python", "-m", "calbot.main"]
