"""history.json: one entry per successfully recorded replay."""
import json
from datetime import date
from pathlib import Path


class History:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        if path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8") or "[]")

    def ids(self) -> set[str]:
        return {e["id"] for e in self.entries}

    def has_entry_for(self, day: date) -> bool:
        return any(e.get("processed_date") == day.isoformat() for e in self.entries)

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic, so a crash never corrupts history
