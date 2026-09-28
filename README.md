# Rocket League replay → MP4

Turns pro replays from ballchasing.com into MP4 videos of the real game. Each video contains 2 games, back to back, of the same player in the same playlist.

**Status:** Phases 1–3 are done: search, download, launching the game and playing the replay with a locked camera. Phase 4 (OBS recording and file naming) is written but not yet tested.

## How it works

1. **Search.** Queries the ballchasing API once per player in `players.txt` for pro, Supersonic Legend, ranked 1v1/2v2/3v3 games.
2. **Pair.** Pairs up unseen games of the same player in the same playlist, played at most `max_gap_days` apart.
3. **Download.** Downloads both `.replay` files into the Rocket League Demos folder.
4. **Launch.** Starts BakkesMod and Rocket League (through Epic) if they aren't running.
5. **Play.** A small BakkesMod plugin (`plugin/RLVid.cpp`) plays each replay. The camera is locked in Player View on the player from `players.txt`, and optionally switches to the Director camera during kickoffs. The replay controls HUD is hidden, while name tags and the scoreboard stay visible.
6. **Record.** OBS records both games into one file, paused while game 2 loads. The video is saved as `YYYY-MM-DD_<player>_<1v1|2v2|3v3>.mp4`.
7. **Track.** Both replays are added to `history.json`, but only after the video has been saved.

Python talks to the plugin through BakkesMod's rcon websocket (port 9002), and the plugin reports its state in `%APPDATA%\bakkesmod\bakkesmod\data\rlvid_status.json`.

## Setup

### 1. Python

```
pip install -r requirements.txt
```

### 2. ballchasing API key

Log in at https://ballchasing.com (Steam login), open https://ballchasing.com/upload, and copy the API key shown there. Then copy `.env.example` to `.env` and fill in `BALLCHASING_API_KEY`.

### 3. Players and settings

- **`players.txt`:** one name per line, `#` for comments. If several listed players are in a match, the camera follows the one listed first.
- **`config.toml`:** filters, paths, camera and pairing settings. With `demos_dir = "auto"`, the Demos folder is found through your Windows Documents folder, including when Documents is in OneDrive.

### 4. Rocket League (Epic) + BakkesMod

1. Install BakkesMod from https://bakkesmod.com. Start it once together with the game, so it downloads its files to `%APPDATA%\bakkesmod`.
2. In the Epic Games Launcher, open **Library**, then **⋯** on Rocket League, then **Manage**. Turn on **Launch Options** and enter `-noeac`. Without this, Epic starts the game with anti-cheat and BakkesMod can't load.
3. Set the in-game video settings once, for example: your recording resolution (1920×1080), a frame rate cap that matches OBS (60), and High Quality render quality with max texture/world detail.

### 5. Build and install the RLVid plugin

You need the MSVC C++ build tools. Visual Studio (2019 or 2022) with "Desktop development with C++", or the VS Build Tools, both work.

```
plugin\build.bat
```

Then, with the game closed:

1. Copy `plugin\build\RLVid.dll` to `%APPDATA%\bakkesmod\bakkesmod\plugins\`.
2. Add `plugin load rlvid` as a new line in `%APPDATA%\bakkesmod\bakkesmod\cfg\plugins.cfg`.
3. Add `rlvid_play`, `rlvid_release` and `rlvid_info` as new lines in `%APPDATA%\bakkesmod\bakkesmod\data\rcon_commands.cfg`. This allows the tool to send those commands over rcon.

The tool reads the rcon password from BakkesMod's `cfg\config.cfg`, so you don't need to copy it anywhere.

Note: BakkesMod's rcon server listens on all network interfaces, protected by that random password. On a home network behind a router this is fine.

### 6. OBS Studio

1. Install OBS Studio 28 or newer from https://obsproject.com. The WebSocket server is built in.
2. **WebSocket:** open **Tools → WebSocket Server Settings**, tick **Enable WebSocket server** and keep port 4455. Click **Show Connect Info**, copy the password, and add it to `.env` as `OBS_WEBSOCKET_PASSWORD=...`.
3. **Scene:** add a source with **+ → Game Capture**, mode **Capture specific window**, window `[RocketLeague.exe] Rocket League`. **Untick "Capture Cursor"** so the mouse never appears in the video. Set Rocket League's display mode to **Borderless**. In exclusive fullscreen, Windows minimizes the game when another window takes focus, and a minimized game records as black.
4. **Audio:** keep Desktop Audio for the game sound, and mute or remove the microphone.
5. **Video:** in **Settings → Video**, set Base and Output resolution to 1920x1080 and FPS to 60.
6. **Output:** in **Settings → Output**, set Output Mode to **Advanced**, then open the **Recording** tab:
   - **Recording Format:** **Hybrid MP4** (OBS 30.2+), or **MP4** on older versions. Hybrid MP4 survives crashes; plain MP4 does not. Don't use MKV: the tool expects the file OBS reports when recording stops, and it moves that file.
   - **Encoder:** your GPU's hardware encoder: NVIDIA NVENC H.264/HEVC, AMD HW H.264, or Intel QuickSync. On an NVIDIA RTX card, use NVENC H.264, Preset **P5: Slow (Good Quality)**, Tuning **High Quality**, Multipass **Two Passes (Quarter Resolution)**, Profile **high**.
   - **Rate control:** CQP/CQ level 18–20 for high quality, or CBR at about 40–50 Mbps for 1080p60.
   - **Recording Path:** any folder. The tool moves the finished file to `output_dir` from `config.toml`.
7. The tool manages OBS itself. With `fresh_start = true` in `[game]` (the default), each run first closes OBS, Rocket League and BakkesMod (after downloading the replays), then starts them again in that order, and leaves them open afterwards. If OBS is recording when a run starts, it asks for confirmation instead of closing, and the run stops with an error rather than cutting that recording off. Keep only one copy of OBS open: with two copies, both try to capture the game and the recording turns black, so the tool refuses to run. If OBS was closed uncleanly it may show a crash/safe-mode dialog on the next start; answer it once, or the run fails with a clear error when the WebSocket doesn't come up.

## Usage

```
python pick.py   # numbered list of unseen game pairs; type a number to record it
python auto.py   # newest unseen pair, no prompts (for Task Scheduler)
```

- **Logs:** each run writes a log file to `logs/`.
- **Failures:** a failed run (API, game, OBS) is logged and nothing is marked as done. A partial recording is left in the OBS recording folder.

## Rate limits

Regular ballchasing accounts can call the replay list 2 times per second and 500 times per hour, and download files 1 per second and 200 per hour. The tool makes one list call per player, waits `min_delay_seconds` (1 s by default) between calls, and backs off when ballchasing returns HTTP 429.
