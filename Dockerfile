FROM python:3.11-slim

# fonts-thai-tlwg supplies a Thai TTF so the corpus can be regenerated inside
# the container (`make dataset`). The generated corpus is committed, so this is
# only needed if you change the generator.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-thai-tlwg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, against a stub package, so this layer is cached and a
# code change does not re-download every wheel. The editable install points at
# /app/src, where the real code lands on the next line. README.md is only
# package metadata here, so an empty one keeps README edits from busting it.
COPY pyproject.toml ./
RUN mkdir -p src/vantage && touch README.md src/vantage/__init__.py \
 && pip install --no-cache-dir -e ".[api,ui]"
COPY src ./src

COPY scripts ./scripts
COPY policy ./policy
COPY eval ./eval
COPY .streamlit ./.streamlit

EXPOSE 8000
CMD ["uvicorn", "vantage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
