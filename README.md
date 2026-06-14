# automatic-twitch-recorder

Watches Twitch streamers and records the ones you select via [streamlink](https://streamlink.github.io).

## Setup

- Python 3.10+
- `pip install -r requirements.txt`
- Create an app at <https://dev.twitch.tv/console/apps> — the first run asks for
  its Client ID and secret and stores them in `config.json`.

## Usage

`python main.py` (or `Twitch VOD saver.bat` on Windows) starts the daemon and
opens the console UI:

| Key | Action |
| --- | ------ |
| ↑ ↓ / j k / 1–9 | navigate |
| Enter | on a watchlist name — record them whenever live; on a recording name — stop/disarm; on `[add new]` — add a streamer |
| D | delete the selected streamer |
| F | set the download folder (`#streamer#` is replaced with the streamer name) |
| Q | quit |

Streamers under **Recording** are captured whenever they are live (● live,
○ offline). Streamers under **Watchlist** are only monitored. The selection,
the download folder, and the credentials persist in `config.json`.

## Scripting

While the app is running, the daemon listens on `127.0.0.1:1234`.
`python atr_cmd.py` offers the same commands as a classic prompt:
`add <name> [quality]`, `remove <name>`, `record <name> [off]`, `list`,
`time <seconds>`, `download_folder <path>`, `exit`.
