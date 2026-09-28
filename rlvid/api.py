"""Minimal ballchasing.com API client with polite rate limiting.

Limits for regular accounts (https://ballchasing.com/doc/api):
  GET /replays            2/s, 500/h
  GET /replays/{id}       2/s, 1000/h
  GET /replays/{id}/file  1/s, 200/h
"""
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class ApiError(Exception):
    pass


class Ballchasing:
    def __init__(self, api_key: str, base_url: str, min_delay: float = 1.0, max_retries: int = 3):
        self.base = base_url.rstrip("/")
        self.min_delay = min_delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers["Authorization"] = api_key
        self._last_call = 0.0

    def _get(self, path: str, params=None, stream=False) -> requests.Response:
        url = f"{self.base}{path}"
        for attempt in range(1, self.max_retries + 1):
            wait = self.min_delay - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                r = self.session.get(url, params=params, stream=stream, timeout=30)
            except requests.RequestException as e:
                log.warning("request failed (%s), attempt %d/%d", e, attempt, self.max_retries)
                time.sleep(5 * attempt)
                continue
            if r.status_code == 429:
                backoff = max(int(r.headers.get("Retry-After") or 0), 2 * attempt)
                log.warning("rate limited by ballchasing, sleeping %ds", backoff)
                time.sleep(backoff)
                continue
            if r.status_code == 401:
                raise ApiError("ballchasing rejected the API key (401), check .env")
            if r.status_code >= 400:
                raise ApiError(f"ballchasing {r.status_code} for {r.url}: {r.text[:200]}")
            return r
        raise ApiError(f"giving up on {url} after {self.max_retries} attempts (rate limit or network)")

    def list_replays(self, **params) -> list[dict]:
        return self._get("/replays", params=params).json().get("list", [])

    def get_replay(self, replay_id: str) -> dict:
        return self._get(f"/replays/{replay_id}").json()

    def download_replay(self, replay_id: str, dest: Path) -> Path:
        """Stream the .replay file to dest (via a .part file so a failed download leaves nothing behind)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        r = self._get(f"/replays/{replay_id}/file", stream=True)
        try:
            with open(part, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    f.write(chunk)
        except (OSError, requests.RequestException) as e:
            part.unlink(missing_ok=True)
            raise ApiError(f"download of {replay_id} failed: {e}") from e
        if part.stat().st_size < 1024:
            part.unlink()
            raise ApiError(f"download of {replay_id} is suspiciously small, not a replay file")
        part.replace(dest)
        log.info("downloaded %s -> %s (%d KB)", replay_id, dest, dest.stat().st_size // 1024)
        return dest
