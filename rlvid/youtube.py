"""Uploading to YouTube with the YouTube Data API (OAuth, "Desktop app" client).

The first time, `python upload.py --login` opens the browser to sign in and
choose the channel; the token is saved and refreshed from then on, so later
uploads (e.g. from Task Scheduler) need no browser.
"""
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHUNK = 32 * 1024 * 1024  # resumable upload in 32 MB pieces
RETRY_STATUS = {500, 502, 503, 504}


class YouTubeError(Exception):
    pass


class QuotaError(YouTubeError):
    """The daily upload limit or API quota is used up: stop, retry tomorrow."""


def connect(client_secrets: Path, token_file: Path, interactive: bool = False):
    """An authorised YouTube API client. Opens the browser only if interactive."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES) if token_file.exists() else None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # revoked or expired refresh token
            log.warning("YouTube token refresh failed: %s", e)
            creds = None
    if not creds or not creds.valid:
        if not interactive:
            raise YouTubeError("not signed in to YouTube; run once: python upload.py --login")
        if not client_secrets.exists():
            raise YouTubeError(f"OAuth client file not found: {client_secrets} (see README, YouTube setup)")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _http_error(e) -> YouTubeError:
    reason = ""
    try:
        import json
        reason = json.loads(e.content)["error"]["errors"][0]["reason"]
    except Exception:
        pass
    if reason in ("quotaExceeded", "uploadLimitExceeded", "dailyLimitExceeded"):
        return QuotaError(f"YouTube limit reached ({reason}); try again tomorrow")
    return YouTubeError(f"YouTube error {e.resp.status} {reason}: {str(e)[:300]}")


def upload(yt, video: Path, title: str, description: str, tags: list[str], privacy: str = "private",
           category: str = "20") -> str:
    """Upload a video; returns its YouTube id. Category 20 = Gaming."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": category},
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=CHUNK, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    response, retries, last_pct = None, 0, -10
    while response is None:
        try:
            status, response = request.next_chunk()
            retries = 0
            if status and status.progress() * 100 >= last_pct + 10:
                last_pct = int(status.progress() * 100)
                print(f"  uploaded {last_pct}%")
        except HttpError as e:
            if e.resp.status not in RETRY_STATUS or retries >= 5:
                raise _http_error(e) from e
            retries += 1
            time.sleep(2 ** retries)
        except (ConnectionError, TimeoutError, OSError) as e:
            if retries >= 5:
                raise YouTubeError(f"upload failed: {e}") from e
            retries += 1
            time.sleep(2 ** retries)
    if "id" not in response:
        raise YouTubeError(f"unexpected upload response: {str(response)[:300]}")
    return response["id"]


def set_thumbnail(yt, video_id: str, image: Path) -> None:
    """Needs a channel verified by phone (YouTube Studio) for custom thumbnails."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image), mimetype="image/jpeg")).execute()
    except HttpError as e:
        raise _http_error(e) from e
