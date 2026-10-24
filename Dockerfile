FROM python:3.11-slim

# fonts-thai-tlwg supplies a Thai TTF so the corpus can be regenerated inside
# the container (`make dataset`). The generated corpus is committed, so this is
# only needed if you change the generator.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-thai-tlwg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -e ".[api]"

COPY scripts ./scripts
COPY policy ./policy
COPY eval ./eval

EXPOSE 8000
CMD ["uvicorn", "vantage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
