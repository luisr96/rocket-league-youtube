"""Upload recorded videos to YouTube (private), then delete the video files.

    python upload.py           upload every video not on YouTube yet
    python upload.py --login   sign in to YouTube once (opens the browser)
"""
import sys
from pathlib import Path

from rlvid import youtube
from rlvid.app import run
from rlvid.config import ConfigError, load_config
from rlvid.uploader import cmd_upload

if __name__ == "__main__":
    if sys.argv[1:] == ["--login"]:
        try:
            s = load_config().raw.get("youtube", {})
            youtube.connect(Path(s.get("client_secrets", "youtube_client_secret.json")),
                            Path(s.get("token_file", "youtube_token.json")), interactive=True)
        except (ConfigError, youtube.YouTubeError) as e:
            sys.exit(f"Error: {e}")
        print("Signed in to YouTube; uploads can now run without the browser.")
    elif sys.argv[1:]:
        sys.exit(__doc__)
    else:
        sys.exit(run(cmd_upload))
