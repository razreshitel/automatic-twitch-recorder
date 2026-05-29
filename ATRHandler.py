import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from jsonschema import validate, ValidationError


class ATRHandler(BaseHTTPRequestHandler):
    _SCHEMA = {
        'type': 'object',
        'properties': {
            'cmd': {'type': 'string'},
            'args': {'type': 'array', 'items': {'type': 'string'}},
        },
        'required': ['cmd', 'args'],
    }

    def log_message(self, format, *args):
        pass

    def _json(self, status, message):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'println': message}).encode())

    def do_POST(self):
        if self.path != '/cmd/':
            self._json(HTTPStatus.NOT_FOUND, 'Not found.')
            return

        length = int(self.headers.get('Content-Length', 0))
        try:
            payload = json.loads(self.rfile.read(length))
            validate(instance=payload, schema=self._SCHEMA)
        except (json.JSONDecodeError, ValidationError) as e:
            self._json(HTTPStatus.BAD_REQUEST, str(e))
            return

        handler = {
            'exit':            self._cmd_exit,
            'start':           self._cmd_start,
            'list':            self._cmd_list,
            'add':             self._cmd_add,
            'remove':          self._cmd_remove,
            'record':          self._cmd_record,
            'state':           self._cmd_state,
            'time':            self._cmd_time,
            'download_folder': self._cmd_download_folder,
        }.get(payload['cmd'])

        if not handler:
            self._json(HTTPStatus.BAD_REQUEST, f"Unknown command: '{payload['cmd']}'.")
            return

        handler(payload['args'])

    def _cmd_exit(self, args):
        self._json(HTTPStatus.OK, self.server.exit())

    def _cmd_start(self, args):
        self._json(HTTPStatus.OK, self.server.start())

    def _cmd_list(self, args):
        live, offline = self.server.get_streamers()
        self._json(HTTPStatus.OK,
                   f"Live: {', '.join(live) or '—'}\nOffline: {', '.join(offline) or '—'}")

    def _cmd_add(self, args):
        if not args:
            self._json(HTTPStatus.BAD_REQUEST, 'Missing streamer name.')
            return
        quality = args[1] if len(args) > 1 else 'best'
        ok, resp = self.server.add_streamer(args[0], quality)
        self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, '\n'.join(resp))

    def _cmd_remove(self, args):
        if not args:
            self._json(HTTPStatus.BAD_REQUEST, 'Missing streamer name.')
            return
        ok, msg = self.server.remove_streamer(args[0])
        self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, msg)

    def _cmd_record(self, args):
        if not args:
            self._json(HTTPStatus.BAD_REQUEST, 'Missing streamer name.')
            return
        active = len(args) < 2 or args[1] != 'off'
        ok, msg = self.server.set_recording(args[0], active)
        self._json(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST, msg)

    def _cmd_state(self, args):
        self._json(HTTPStatus.OK, json.dumps({
            'streamers': self.server.get_state(),
            'download_folder': self.server.download_folder,
        }))

    def _cmd_time(self, args):
        if not args:
            self._json(HTTPStatus.BAD_REQUEST, 'Missing interval.')
            return
        try:
            self._json(HTTPStatus.OK, self.server.set_interval(int(args[0])))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, f"'{args[0]}' is not a valid number.")

    def _cmd_download_folder(self, args):
        if not args:
            self._json(HTTPStatus.BAD_REQUEST, 'Missing path.')
            return
        self._json(HTTPStatus.OK, self.server.set_download_folder(args[0].strip()))
