"""Daily job (run by Task Scheduler): record a new video, then upload everything pending.

Uploading runs even if recording found nothing or failed, so earlier videos
whose upload failed are retried.
"""
import sys

from rlvid.app import cmd_auto, run
from rlvid.uploader import cmd_upload


def cmd_daily(cfg, api, history) -> int:
    recorded = cmd_auto(cfg, api, history)
    uploaded = cmd_upload(cfg, api, history)
    return recorded or uploaded


if __name__ == "__main__":
    sys.exit(run(cmd_daily))
