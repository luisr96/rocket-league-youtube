"""history.json: one entry per successfully recorded replay."""
import json
from pathlib import Path


class History:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        if path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8") or "[]")

    def ids(self) -> set[str]:
        return {e["id"] for e in self.entries}

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        self._save()

    def for_video(self, video: str) -> list[dict]:
        return [e for e in self.entries if e.get("video") == video]

    def update_video(self, video: str, fields: dict) -> None:
        """Add fields (e.g. the YouTube id) to the entries of both games of a video."""
        for e in self.for_video(video):
            e.update(fields)
        self._save()

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.entries, indent=2), encoding="utf-8")
        tmp.replace(self.path)  # atomic, so a crash never corrupts history
