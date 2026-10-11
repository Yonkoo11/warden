# Warden: one process (web + scheduler) with the Binance Agentic Wallet CLI alongside.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 NODE_MAJOR=20
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g @binance/agentic-wallet@1.10.0 \
 && rm -rf "$(npm root -g)/@binance/agentic-wallet/node_modules/@github/keytar" "$(npm root -g)/@github/keytar" \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY warden.toml ./
COPY warden ./warden
# The wallet session lives on the persistent volume; the CLI reads BINANCE_BAW_DIR (read from its code, 2026-10-05).
ENV WARDEN_DATA=/data BINANCE_BAW_DIR=/data/baw RUN_SCHEDULER=1 PORT=8080
RUN mkdir -p /data && chmod 700 /data
EXPOSE 8080
CMD ["sh", "-c", "mkdir -p /data/baw && chmod 700 /data/baw && exec uvicorn warden.web:app --host 0.0.0.0 --port ${PORT}"]
