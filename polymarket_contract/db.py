"""Storage layer — any Postgres, via a plain DATABASE_URL connection string.

This works for Supabase (Settings -> Database -> Connection string -> URI),
Neon, Railway, or a local Postgres install — one code path, no vendor SDK.
Keyed by contract `title` as requested. Note: titles are Polymarket's own
question text and could theoretically collide or be reused — `condition_id`
is stored alongside as the collision-proof alternative if you ever need one.

Nothing here is a mock: if DATABASE_URL isn't set, or the connection fails,
every function returns a clear {"ok": False, "error": "..."} rather than
pretending to have saved something.
"""
from __future__ import annotations
import os
import pathlib

try:
    from dotenv import load_dotenv
    load_dotenv(pathlib.Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

DATABASE_URL = os.getenv("DATABASE_URL")

EMBED_DIM = 384  # must match embeddings.py's model output (all-MiniLM-L6-v2)

# Lean schema — only what's useful for understanding a contract, plus a
# timestamp. `data` still holds the full ContractDocument (incl. outcomes[]
# and notes[]) as JSONB so nothing is lost, it's just not spread across a
# pile of promoted columns that aren't worth indexing/filtering on.
SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS polymarket_contracts (
    title           TEXT PRIMARY KEY,
    url             TEXT,
    condition_id    TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL,
    summary         TEXT,
    volume          DOUBLE PRECISION,
    volume_24hr     DOUBLE PRECISION,
    liquidity       DOUBLE PRECISION,
    active          BOOLEAN,
    closed          BOOLEAN,
    data            JSONB NOT NULL,        -- full ContractDocument (outcomes, notes, etc)
    embedding       vector({EMBED_DIM}),   -- local sentence-transformer vector
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_polymarket_contracts_condition_id ON polymarket_contracts (condition_id);
CREATE INDEX IF NOT EXISTS idx_polymarket_contracts_embedding
    ON polymarket_contracts USING hnsw (embedding vector_cosine_ops);
"""


def configured() -> bool:
    return bool(DATABASE_URL)


def _connect():
    import psycopg2
    return psycopg2.connect(DATABASE_URL)


def init_schema() -> dict:
    if not configured():
        return {"ok": False, "error": "DATABASE_URL not set — see .env.example"}
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _vec_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


def save_contract(doc, embedding: list[float] | None = None) -> dict:
    """doc: a ContractDocument (pydantic model) from polymarket_contract.py.
    embedding: optional 384-dim vector (embeddings.embed(...)) for semantic search."""
    if not configured():
        return {"ok": False, "error": "DATABASE_URL not set — see .env.example"}
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)  # idempotent — ensures the table + extension exist
            cur.execute(
                """
                INSERT INTO polymarket_contracts
                    (title, url, condition_id, fetched_at, summary, volume,
                     volume_24hr, liquidity, active, closed, data, embedding, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (title) DO UPDATE SET
                    url = EXCLUDED.url,
                    condition_id = EXCLUDED.condition_id,
                    fetched_at = EXCLUDED.fetched_at,
                    summary = EXCLUDED.summary,
                    volume = EXCLUDED.volume,
                    volume_24hr = EXCLUDED.volume_24hr,
                    liquidity = EXCLUDED.liquidity,
                    active = EXCLUDED.active,
                    closed = EXCLUDED.closed,
                    data = EXCLUDED.data,
                    embedding = EXCLUDED.embedding,
                    updated_at = now();
                """,
                (doc.title, doc.url, doc.condition_id, doc.fetched_at, doc.summary,
                 doc.volume, doc.volume_24hr, doc.liquidity, doc.active, doc.closed,
                 doc.model_dump_json(), _vec_literal(embedding)),
            )
        conn.close()
        return {"ok": True, "title": doc.title}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search_similar(query_text: str, k: int = 10) -> list[dict]:
    """Cosine-similarity semantic search over stored contracts.
    Embeds `query_text` locally (embeddings.py) and ranks by pgvector's
    cosine distance operator (<=>); returns (1 - distance) as `similarity`."""
    if not configured():
        return []
    from embeddings import embed
    qvec = _vec_literal(embed(query_text))
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT title, url, summary, volume_24hr,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM polymarket_contracts
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (qvec, qvec, k),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return [{"error": str(e)}]


def get_contract(title: str) -> dict | None:
    if not configured():
        return None
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute("SELECT data FROM polymarket_contracts WHERE title = %s", (title,))
            row = cur.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def list_contracts(limit: int = 50) -> list[dict]:
    if not configured():
        return []
    try:
        conn = _connect()
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT title, url, summary, volume_24hr, updated_at "
                "FROM polymarket_contracts ORDER BY updated_at DESC LIMIT %s", (limit,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


if __name__ == "__main__":
    import sys, json as _json
    print("DATABASE_URL configured:", configured())
    if not configured():
        raise SystemExit(0)
    print(init_schema())
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f"\nSemantic search: {query!r}")
        for r in search_similar(query, k=10):
            print(f"  {r.get('similarity', 0):.3f}  {r.get('title')}  [{r.get('url')}]")
    else:
        print("Recent contracts:", _json.dumps(list_contracts(10), default=str, indent=1))
