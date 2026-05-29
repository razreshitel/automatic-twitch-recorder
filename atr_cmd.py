import cmd
import sys

import requests


class AtrCmd(cmd.Cmd):
    prompt = '> '

    def _send(self, command, *args):
        r = requests.post('http://127.0.0.1:1234/cmd/', json={'cmd': command, 'args': list(args)})
        print(r.json().get('println', ''))
        return r.ok

    def do_add(self, line):
        args = line.split()
        self._send('add', *args)

    def help_add(self):
        print('add <streamer> [quality]  — add streamer to watchlist (default quality: best)')

    def do_remove(self, line):
        self._send('remove', line.strip())

    def help_remove(self):
        print('remove <streamer>  — remove streamer from watchlist, stops recording if active')

    def do_record(self, line):
        self._send('record', *line.split())

    def help_record(self):
        print('record <streamer> [off]  — start recording a streamer when live (or stop with off)')

    def do_list(self, line):
        self._send('list')

    def help_list(self):
        print('list  — show all watched streamers (live and offline)')

    def do_start(self, line):
        self._send('start')

    def help_start(self):
        print('start  — start checking and recording streamers')

    def do_time(self, line):
        self._send('time', line.strip())

    def help_time(self):
        print('time <seconds>  — set check interval (default: 30)')

    def do_download_folder(self, line):
        self._send('download_folder', line.strip())

    def help_download_folder(self):
        print('download_folder <path>  — set save location (#streamer# is replaced with the streamer name)')

    def do_exit(self, line):
        self._send('exit')
        sys.exit()

    def help_exit(self):
        print('exit  — stop all recordings and exit')

    def do_EOF(self, line):
        self.do_exit(line)

    def cmdloop_with_keyboard_interrupt(self):
        try:
            self.cmdloop()
        except KeyboardInterrupt:
            self.do_exit('')


if __name__ == '__main__':
    AtrCmd().cmdloop_with_keyboard_interrupt()
