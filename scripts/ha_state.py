"""Report what this integration's entities currently hold.

Reads the recorder's latest state per entity, but only for entities Home
Assistant still has registered. Reading the history alone is misleading: rows
survive a rename or a removal, so a deleted entity looks alive and a restored
`unavailable` looks like a fresh reading. That mistake cost an hour of
misdiagnosis once already.

Runs inside the Home Assistant container, driven by `scripts/ha-dev.sh state`.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3

CONFIG = pathlib.Path("/config")
DOMAIN = "plusportal"


def registered() -> list[str]:
    """Entity ids Home Assistant currently has for this integration."""
    registry = json.loads(
        (CONFIG / ".storage" / "core.entity_registry").read_text(encoding="utf-8")
    )
    return sorted(
        entity["entity_id"]
        for entity in registry["data"]["entities"]
        if entity.get("platform") == DOMAIN
    )


def latest_states(entity_ids: list[str]) -> dict[str, str]:
    """Most recent recorded state for each entity id."""
    database = CONFIG / "home-assistant_v2.db"
    if not database.exists() or not entity_ids:
        return {}

    db = sqlite3.connect(database)
    placeholders = ",".join("?" * len(entity_ids))
    rows = db.execute(
        f"""
        SELECT m.entity_id, s.state
        FROM states s JOIN states_meta m ON s.metadata_id = m.metadata_id
        WHERE s.state_id IN (SELECT MAX(state_id) FROM states GROUP BY metadata_id)
          AND m.entity_id IN ({placeholders})
        """,
        entity_ids,
    )
    return dict(rows)


def main() -> None:
    """Print each registered entity and what it holds."""
    entity_ids = registered()
    if not entity_ids:
        print("  no entities registered — is the integration configured?")
        return

    states = latest_states(entity_ids)
    width = max(len(state) for state in states.values()) if states else 4
    for entity_id in entity_ids:
        state = states.get(entity_id, "(no state recorded yet)")
        print(f"  {state:>{width}}  {entity_id}")


if __name__ == "__main__":
    main()
