# One image for both processes of this repository (ADR-0032):
#   engine API      uvicorn umai_decision_engine.api.app:app --host 0.0.0.0 --port 9100
#   policy service  uvicorn umai_decision_engine.control_plane.app:app --host 0.0.0.0 --port 9200
# Build: docker build -t umai-decision-engine .
FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install .

FROM python:3.12-slim

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    REGEX_TIMEOUT_SECONDS=1 \
    UMAI_DE_ENV=production \
    UMAI_DE_POLICY_DIR=/data/policies \
    UMAI_DE_CONTROL_DB_PATH=/data/control/control.sqlite

RUN useradd --system --uid 10001 --home-dir /app umai \
    && mkdir -p /data/policies /data/control \
    && chown -R umai /data
COPY --from=build /opt/venv /opt/venv
# Seed bundles for an empty policy volume (deploy/vm/seed-policies.sh); never read in place.
COPY --chown=umai policies /app/seed-policies
# The product's plans (contract entitlements, ADR-0043); used when UMAI_DE_PLANS_FILE names it.
COPY --chown=umai deploy/plans.yaml /app/plans.yaml
WORKDIR /app
USER umai

EXPOSE 9100 9200 9300
# --no-proxy-headers: uvicorn would rewrite the client address from X-Forwarded-For on its own;
# admission reads it only from UMAI_DE_TRUSTED_PROXIES (ADR-0046).
CMD ["uvicorn", "umai_decision_engine.api.app:app", "--host", "0.0.0.0", "--port", "9100", "--no-server-header", "--no-proxy-headers"]
