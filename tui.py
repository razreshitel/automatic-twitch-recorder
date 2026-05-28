import curses
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


def _get_rows():
    """Returns list of (name_or_sentinel, is_live)."""
    try:
        r = requests.post(
            'http://127.0.0.1:1234/cmd/',
            json={'cmd': 'list', 'args': []},
            timeout=1,
        )
        text = r.json().get('println', '')
    except Exception:
        return None

    live, offline = [], []
    for line in text.split('\n'):
        if line.startswith('Live: '):
            live = [s.strip() for s in line[6:].split(',') if s.strip() and s.strip() != '—']
        elif line.startswith('Offline: '):
            offline = [s.strip() for s in line[9:].split(',') if s.strip() and s.strip() != '—']

    return [(n, True) for n in live] + [(n, False) for n in offline] + [(_SENTINEL, False)]


def _prompt_input(stdscr, prompt):
    h, w = stdscr.getmaxyx()
    stdscr.addstr(h - 2, 2, (prompt + ' ' * w)[:w - 3])
    curses.echo()
    curses.curs_set(1)
    stdscr.move(h - 2, 2 + len(prompt))
    stdscr.timeout(-1)  # block while typing; the 1s refresh timeout injects junk bytes
    val = stdscr.getstr(h - 2, 2 + len(prompt), w - len(prompt) - 4).decode(errors='ignore').strip()
    stdscr.timeout(1000)
    curses.noecho()
    curses.curs_set(0)
    return val


def _draw(stdscr, rows, cursor, status):
    stdscr.erase()
    h, w = stdscr.getmaxyx()

    stdscr.addstr(1, 2, 'Automatic Twitch Recorder', curses.A_BOLD)
    stdscr.addstr(2, 2, '─' * min(25, w - 4))

    for i, (name, is_live) in enumerate(rows):
        y = 4 + i
        if y >= h - 3:
            break
        attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
        if name is _SENTINEL:
            line = f'  {i + 1}.   [add new]'
        elif is_live:
            line = f'  {i + 1}. ★ {name:<22} recording'
        else:
            line = f'  {i + 1}.   {name:<22} offline'
        stdscr.addstr(y, 0, line.ljust(w - 1)[:w - 1], attr)

    if status:
        stdscr.addstr(h - 2, 2, status[:w - 4])

    hint = '↑↓ / number — navigate    Enter — select / add    D — remove    Q — quit'
    stdscr.addstr(h - 1, 2, hint[:w - 4], curses.A_DIM)

    stdscr.refresh()


def run(stdscr):
    curses.curs_set(0)
    stdscr.timeout(1000)

    cursor = 0
    status = ''

    while True:
        rows = _get_rows()
        if rows is None:
            rows = [(_SENTINEL, False)]
            status = 'Daemon unreachable.'

        cursor = min(cursor, len(rows) - 1)
        _draw(stdscr, rows, cursor, status)

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
            name, _ = rows[cursor]
            if name is _SENTINEL:
                new = _prompt_input(stdscr, 'Streamer name:')
                status = _api('add', new) if new else ''
            else:
                status = _api('remove', name)

        elif key in (ord('d'), ord('D')):
            name, _ = rows[cursor]
            if name is not _SENTINEL:
                status = _api('remove', name)

        elif key in (ord('q'), ord('Q')):
            break


def main():
    curses.wrapper(run)
