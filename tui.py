import curses
import json

import requests

_SENTINEL = object()  # marks the [add new] row


def _api(cmd, *args):
    try:
        r = requests.post(
            'http://127.0.0.1:1234/cmd/',
            json={'cmd': cmd, 'args': list(args)},
            timeout=2,
        )
        return r.json().get('println', '')
    except Exception as e:
        return f'Error: {e}'


def _get_state():
    """Returns (rows, download_folder); rows is active streamers, then
    watchlist, then [add new]. Each streamer row is a dict:
    {'name', 'active', 'live', 'recording'}.
    """
    try:
        r = requests.post(
            'http://127.0.0.1:1234/cmd/',
            json={'cmd': 'state', 'args': []},
            timeout=1,
        )
        state = json.loads(r.json().get('println', '{}'))
        streamers = state['streamers']
    except Exception:
        return None, ''

    streamers.sort(key=lambda s: (not s['recording'], not s['live'], s['name']))
    recording = [s for s in streamers if s['active']]
    watchlist = [s for s in streamers if not s['active']]
    return recording + watchlist + [_SENTINEL], state.get('download_folder', '')


def _prompt_input(stdscr, prompt):
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h - 2, 2, (prompt + ' ' * w)[:w - 3])
    curses.echo()
    curses.curs_set(1)
    stdscr.timeout(-1)  # block while typing; the 1s refresh timeout injects junk bytes
    val = stdscr.getstr(h - 2, 2 + len(prompt), w - len(prompt) - 4).decode(errors='ignore').strip()
    stdscr.timeout(1000)
    curses.noecho()
    curses.curs_set(0)
    return val


def _draw(stdscr, rows, cursor, status, folder):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    stdscr.addstr(1, 2, 'Automatic Twitch Recorder', curses.A_BOLD)
    stdscr.addstr(2, 2, '─' * min(25, w - 4))
    if folder:
        stdscr.addstr(3, 2, f'folder: {folder}'[:w - 4], curses.A_DIM)

    y = 5
    section = None
    for i, row in enumerate(rows):
        if row is _SENTINEL:
            wanted = 'Watchlist'
        else:
            wanted = 'Recording' if row['active'] else 'Watchlist'
        if wanted != section:
            section = wanted
            if y < h - 3:
                stdscr.addstr(y, 1, section, curses.A_BOLD | curses.A_DIM)
                y += 1

        if y >= h - 3:
            break
        attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
        if row is _SENTINEL:
            line = f'  {i + 1}.   [add new]'
        else:
            mark = '●' if row['live'] else '○'
            if row['recording']:
                state = 'live — recording'
            elif row['active']:
                state = 'live — starting…' if row['live'] else 'offline — waiting'
            else:
                state = 'live' if row['live'] else 'offline'
            line = f'  {i + 1}. {mark} {row["name"]:<22} {state}'
        stdscr.addstr(y, 0, line.ljust(w - 1)[:w - 1], attr)
        y += 1

    if status:
        stdscr.addstr(h - 2, 2, status[:w - 4])

    hint = '↑↓ / number — navigate    Enter — record on/off / add    D — remove    F — folder    Q — quit'
    stdscr.addstr(h - 1, 2, hint[:w - 4], curses.A_DIM)

    stdscr.refresh()


def run(stdscr):
    curses.curs_set(0)
    stdscr.timeout(1000)

    cursor = 0
    status = ''

    while True:
        rows, folder = _get_state()
        if rows is None:
            rows = [_SENTINEL]
            status = 'Daemon unreachable.'

        cursor = min(cursor, len(rows) - 1)
        _draw(stdscr, rows, cursor, status, folder)

        key = stdscr.getch()
        if key == -1:
            status = ''
            continue

        if key in (curses.KEY_UP, ord('k')):
            cursor = max(0, cursor - 1)
            status = ''

        elif key in (curses.KEY_DOWN, ord('j')):
            cursor = min(len(rows) - 1, cursor + 1)
            status = ''

        elif ord('1') <= key <= ord('9'):
            idx = key - ord('1')
            if idx < len(rows):
                cursor = idx
            status = ''

        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            row = rows[cursor]
            if row is _SENTINEL:
                new = _prompt_input(stdscr, 'Streamer name:')
                status = _api('add', new) if new else ''
            elif row['active']:
                status = _api('record', row['name'], 'off')
            else:
                status = _api('record', row['name'])

        elif key in (ord('d'), ord('D')):
            row = rows[cursor]
            if row is not _SENTINEL:
                status = _api('remove', row['name'])

        elif key in (ord('f'), ord('F')):
            new = _prompt_input(stdscr, 'Download folder (#streamer# → name):')
            status = _api('download_folder', new) if new else ''

        elif key in (ord('q'), ord('Q')):
            break


def main():
    curses.wrapper(run)
