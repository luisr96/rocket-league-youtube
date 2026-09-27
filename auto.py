"""Auto mode: process the newest unseen match with no prompts (run by Task Scheduler)."""
import sys

from rlvid.app import cmd_auto, run

if __name__ == "__main__":
    sys.exit(run(cmd_auto))
