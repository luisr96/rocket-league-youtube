"""Daily job (run by Task Scheduler): record a new video, then upload everything pending.

Uploading runs even if recording found nothing or failed, so earlier videos
whose upload failed are retried.
"""
import logging
import sys

from rlvid.app import cmd_auto, run
from rlvid.uploader import cmd_upload

log = logging.getLogger("rlvid")


def cmd_daily(cfg, api, history) -> int:
    try:
        recorded = cmd_auto(cfg, api, history)
    except Exception as e:  # game, OBS, API, ...: log it, but still upload what's waiting
        log.exception("recording failed: %s", e)
        print(f"ERROR: recording failed ({e}); uploading anything already recorded.")
        recorded = 1
    uploaded = cmd_upload(cfg, api, history)
    return recorded or uploaded


if __name__ == "__main__":
    sys.exit(run(cmd_daily))
