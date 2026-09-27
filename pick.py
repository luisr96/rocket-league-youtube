"""Manual mode: list unseen matches and choose one to process."""
import sys

from rlvid.app import cmd_pick, run

if __name__ == "__main__":
    sys.exit(run(cmd_pick))
