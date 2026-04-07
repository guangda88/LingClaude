FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml VERSION README.md ./
COPY lingclaude/ lingclaude/

RUN pip install --no-cache-dir .

ENTRYPOINT ["python3", "-m", "lingclaude.cli"]
CMD ["--help"]
