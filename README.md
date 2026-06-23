# Case Search Assistant

`类案检索助手` is a local-first legal case retrieval prototype. This repository contains the backend, frontend, design notes, and evaluation artifacts used during iterative development.

## Repository Layout

- `apps/api` - FastAPI backend, retrieval pipeline, evaluation scripts, and tests
- `apps/web` - Vite/React frontend
- `infra` - local development infrastructure
- `docs/development` - verification notes, evaluation outputs, and engineering records
- `落地设计文档` - product and implementation planning documents

## Quick Start

1. Copy `.env.example` to `.env` and fill local secrets.
2. Start local dependencies:

   ```bash
   docker compose -f infra/docker-compose.yml up -d
   ```

3. Start the API:

   ```bash
   cd apps/api
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   uvicorn app.main:app --reload --port 8000
   ```

4. Start the web app:

   ```bash
   cd apps/web
   npm install
   npm run dev
   ```

## Local Data Paths

- `CHROMA_PERSIST_DIR` defaults to `./data/chroma`.
- If your Windows checkout path contains non-ASCII characters and Chroma/HNSW behaves poorly, set `CHROMA_PERSIST_DIR` to an ASCII-only directory.
- LeCaRDv2 helper scripts now default to `data/external/LeCaRDv2-main`. You can also pass `--lecard-root` or `--corpus` explicitly.

## Public Repo Notes

- Secrets stay in `.env` or environment variables and are excluded from Git.
- Logs, virtual environments, local databases, build outputs, and transient verification artifacts are ignored.
- Historical documents were sanitized to remove machine-specific paths before publication.

## More Context

- Development notes: `docs/development/README-day0.md`
- Product planning index: `落地设计文档/00-文档索引.md`
