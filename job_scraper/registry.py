"""Database overlay: preserve TOML entries while managing discovered boards."""

import json
from datetime import UTC, datetime

from .config import SourceConfig


def effective_sources(store, configured):
    result = {(s.source, s.scope): s.model_copy() for s in configured}
    for row in store.db.execute(
        "SELECT * FROM company_boards WHERE approved=1 AND verified_at IS NOT NULL"
    ):
        source = SourceConfig(
            source=row["source"],
            board=row["board"],
            region=row["region"],
            company=row["company"],
            enabled=bool(row["enabled"]),
        )
        result.setdefault((source.source, source.scope), source)
    for row in store.db.execute("SELECT * FROM source_overrides"):
        if (row["source"], row["board"]) in result:
            result[(row["source"], row["board"])].enabled = bool(row["enabled"])
    return list(result.values())


def save_candidate(store, source, discovery_url, board_url, verified, evidence, approve=False):
    stamp = datetime.now(UTC).isoformat() if verified else None
    with store.db:
        store.db.execute(
            """INSERT INTO company_boards(source,board,region,company,discovery_url,board_url,verified_at,approved,enabled,evidence)
            VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source,board,region) DO UPDATE SET
            verified_at=coalesce(excluded.verified_at,company_boards.verified_at), evidence=excluded.evidence,
            approved=max(company_boards.approved,excluded.approved)
            """,
            (
                source.source,
                source.board,
                source.region,
                source.company,
                discovery_url,
                board_url,
                stamp,
                int(verified and approve),
                0,
                json.dumps(evidence),
            ),
        )
    return {
        "source": source.source,
        "board": source.board,
        "region": source.region,
        "verified": verified,
        "approval_required": not (verified and approve),
        "enabled": False,
        "discovery_url": discovery_url,
        "evidence": evidence,
    }


def approve_board(store, source, board, region="global"):
    with store.db:
        row = store.db.execute(
            "SELECT verified_at FROM company_boards WHERE source=? AND board=? AND region=?",
            (source, board, region),
        ).fetchone()
        if not row or not row[0]:
            raise ValueError(
                "Board must pass endpoint verification before approval; rediscover it to retry"
            )
        store.db.execute(
            "UPDATE company_boards SET approved=1 WHERE source=? AND board=? AND region=?",
            (source, board, region),
        )


def set_enabled(store, configured, source, board, enabled, region="global"):
    scope = f"{region}:{board}" if source == "lever" else board
    if (source, scope) not in {(s.source, s.scope) for s in effective_sources(store, configured)}:
        raise ValueError("Unknown or unapproved board")
    with store.db:
        store.db.execute(
            "INSERT INTO source_overrides VALUES (?,?,?) ON CONFLICT(source,board) DO UPDATE SET enabled=excluded.enabled",
            (source, scope, int(enabled)),
        )
