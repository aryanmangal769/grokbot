"""Seed the four demo personas.

Personalization for the demo is mock personas (see design doc): the X API v2 has
no interests endpoint, so instead of deriving interests we pick a persona whose
`interests` tag list filters which events surface in "Markets for you".
"""

from __future__ import annotations

import json

from insights.db import connect, init_db

PERSONAS = [
    {
        "id": "crypto-degen",
        "name": "Crypto Degen",
        "avatar": "🦍",
        "interests": ["crypto", "technology", "economics"],
    },
    {
        "id": "politics-junkie",
        "name": "Politics Junkie",
        "avatar": "🏛️",
        "interests": ["politics", "economics", "world"],
    },
    {
        "id": "sports-bettor",
        "name": "Sports Bettor",
        "avatar": "🏆",
        "interests": ["sports", "pop-culture"],
    },
    {
        "id": "ai-tech",
        "name": "AI / Tech",
        "avatar": "🤖",
        "interests": ["technology", "crypto", "science"],
    },
]


def seed() -> None:
    init_db()
    with connect() as conn:
        for p in PERSONAS:
            conn.execute(
                """INSERT INTO personas (id, name, avatar, interests)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       name=excluded.name,
                       avatar=excluded.avatar,
                       interests=excluded.interests""",
                (p["id"], p["name"], p["avatar"], json.dumps(p["interests"])),
            )
    print(f"Seeded {len(PERSONAS)} personas.")


if __name__ == "__main__":
    seed()
