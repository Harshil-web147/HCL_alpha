FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
RUN uv pip install --system --no-cache --upgrade pip setuptools wheel fastapi pydantic httpx pytest ruff bandit pip-audit packaging
# A real vulnerability audit is prepared while networking is available. Runtime
# verifies inventory and freshness before using this immutable offline snapshot.
RUN python -c "import datetime,importlib.metadata,json,pathlib,subprocess; p=subprocess.run(['pip-audit','--local','--format','json','--output','/opt/audit.json']); pathlib.Path('/opt/audit.json').touch(exist_ok=True); pathlib.Path('/opt/audit-meta.json').write_text(json.dumps({'exit_code':p.returncode,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'inventory':sorted((d.metadata['Name'].lower(),d.version) for d in importlib.metadata.distributions())}))"
USER 65534:65534
WORKDIR /tmp
