FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SDLC_DATA_DIR=/data \
    SDLC_WORKSPACE_ROOT=/workspaces

WORKDIR /srv

# git is used for optional workspace snapshots; nothing else needs system packages
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY config ./config
COPY tests ./tests
COPY pyproject.toml README.md ./

RUN mkdir -p /data /workspaces && useradd -m copilot && chown -R copilot /srv /data /workspaces
USER copilot

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
