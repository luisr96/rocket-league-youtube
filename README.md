# Rocket League replay → MP4

Turns pro replays from ballchasing.com into MP4 videos of the real game, one per day.

Status: **Phase 1** (search, `players.txt`, `history.json`, `pick` listing). Download and recording come in later phases.

## Setup

1. Install Python 3.11+ and the dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Get a ballchasing API key: log in at https://ballchasing.com (Steam login), open https://ballchasing.com/upload, and copy the API key shown there.
3. Copy `.env.example` to `.env` and paste the key: `BALLCHASING_API_KEY=...`
4. Edit `players.txt`: one name per line, `#` for comments. The order matters: if several listed players are in a match, the camera follows the first one.
5. Change `config.toml` if you want different filters or paths. By default (`demos_dir = "auto"`) replays are saved to the Demos folder under your Windows Documents folder, including when Documents is in OneDrive.

## Usage

```
python pick.py   # numbered list of up to 10 unseen matches; type a number
python auto.py   # newest unseen match, no prompts
```

Each run writes a log file to `logs/`. A replay is added to `history.json` only after its video has been recorded successfully.

## Rate limits

Regular ballchasing accounts can call the replay list 2 times per second and 500 times per hour. The tool makes one list call per player, waits `min_delay_seconds` (1 s by default) between calls, and backs off when ballchasing returns HTTP 429.
