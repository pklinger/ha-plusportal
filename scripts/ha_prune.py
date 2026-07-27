"""Remove Home Assistant state left behind by a rename.

Renaming a sensor's key or a statistic id does not remove the old one: the
entity lingers as `unavailable`, and the orphaned statistic series stays in the
Energy dashboard's picker under the same display name as its replacement —
indistinguishable from it.

Runs inside the Home Assistant container, against a stopped instance, driven by
`scripts/ha-dev.sh prune`. It only ever touches this integration's own records.
"""

from __future__ import annotations

import json
import pathlib
import re
import sqlite3

CONFIG = pathlib.Path("/config")
DOMAIN = "plusportal"


#: The integration logs every series it writes at debug level, which
#: `scripts/ha-dev.sh up` enables.
IMPORTED = re.compile(r"Imported \d+ statistics for (plusportal:[a-z0-9_]+)")


def currently_written() -> set[str]:
    """Statistic ids the integration wrote during this run of Home Assistant.

    Read from the log rather than inferred from the id's shape. Two pattern
    rules were tried first — counting segments, then checking whether the
    channel segment was numeric — and both misjudged an id the moment the
    naming scheme changed. Observing what the integration actually does cannot
    drift away from what it actually does.
    """
    log = CONFIG / "home-assistant.log"
    if not log.exists():
        return set()
    return set(IMPORTED.findall(log.read_text(encoding="utf-8", errors="replace")))


def prune_entities() -> int:
    """Drop registry entries whose translation key the integration no longer declares."""
    strings = json.loads(
        (CONFIG / "custom_components" / DOMAIN / "strings.json").read_text(encoding="utf-8")
    )
    live = set(strings["entity"]["sensor"])

    registry = CONFIG / ".storage" / "core.entity_registry"
    data = json.loads(registry.read_text(encoding="utf-8"))
    entities = data["data"]["entities"]

    kept = [
        entity
        for entity in entities
        if entity.get("platform") != DOMAIN or entity.get("translation_key") in live
    ]
    removed = len(entities) - len(kept)
    if removed:
        data["data"]["entities"] = kept
        registry.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return removed


def prune_statistics() -> list[str]:
    """Drop statistic series whose id predates account scoping."""
    database = CONFIG / "home-assistant_v2.db"
    if not database.exists():
        return []

    keep = currently_written()
    if not keep:
        # Nothing observed, so nothing can be judged orphaned. Deleting on a
        # guess would destroy history that is not recoverable.
        print("  (no imports seen in the log; leaving statistics alone)")
        return []

    db = sqlite3.connect(database)
    orphans = [
        statistic_id
        for (statistic_id,) in db.execute(
            "SELECT statistic_id FROM statistics_meta WHERE source = ?", (DOMAIN,)
        )
        if statistic_id not in keep
    ]

    for statistic_id in orphans:
        (metadata_id,) = db.execute(
            "SELECT id FROM statistics_meta WHERE statistic_id = ?", (statistic_id,)
        ).fetchone()
        for table in ("statistics", "statistics_short_term"):
            db.execute(f"DELETE FROM {table} WHERE metadata_id = ?", (metadata_id,))
        db.execute("DELETE FROM statistics_meta WHERE id = ?", (metadata_id,))
    db.commit()
    return orphans


def main() -> None:
    """Report what was removed."""
    print(f"  entities removed: {prune_entities()}")
    orphans = prune_statistics()
    print(f"  statistics removed: {', '.join(orphans) if orphans else 'none'}")


if __name__ == "__main__":
    main()
