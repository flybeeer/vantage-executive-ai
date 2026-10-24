-- Runs once on first Postgres init (mounted into docker-entrypoint-initdb.d).
-- Only enables the pgvector extension; the application creates tables via
-- db.session.init_schema() so the embedding dimension comes from config.
CREATE EXTENSION IF NOT EXISTS vector;
