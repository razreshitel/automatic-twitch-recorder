import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path

import twitch
from utils import (
    get_client_id,
    StreamQualities,
    get_saved_streamers,
    save_streamers,
    get_saved_download_folder,
    save_download_folder,
)
from watcher import Watcher

log = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_FOLDER = str(Path.home() / 'streams' / '#streamer#')


class Daemon(HTTPServer):
    check_interval = 30

    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        # self.streamers and self.watched_streamers are mutated from the HTTP
        # handler thread, the check timer thread, and watcher-pool callbacks.
        # _lock (re-entrant, since some locked methods call others) serialises
        # every access to them.
        self._lock = threading.RLock()
        self.streamers = {}
        self.watched_streamers = {}
        self.download_folder = get_saved_download_folder() or DEFAULT_DOWNLOAD_FOLDER
        self.kill = False
        self.started = False
        self.pool = ThreadPoolExecutor()
        self.client_id = get_client_id()
        for entry in get_saved_streamers():
            self.add_streamer(entry['name'], active=entry['active'])

    def add_streamer(self, streamer, quality=StreamQualities.BEST.value, active=False):
        streamer = streamer.lower()
        valid_qualities = [q.value for q in StreamQualities]
        if quality not in valid_qualities:
            return False, [f"Invalid quality '{quality}'.", f"Options: {valid_qualities}"]

        user_info = twitch.get_user_info(streamer)
        if not user_info:
            return False, [f"Streamer '{streamer}' not found."]

        with self._lock:
            self.streamers[streamer] = {
                'preferred_quality': quality,
                'user_info': user_info[0],
                'active': active,
            }
            self._persist()
        return True, [f"Added '{streamer}' to watchlist."]

    def set_recording(self, streamer, active):
        streamer = streamer.lower()
        with self._lock:
            if streamer in self.watched_streamers:
                if active:
                    return True, f"'{streamer}' is already recording."
                entry = self.watched_streamers[streamer]
                entry['watcher'].quit()
                sd = entry['streamer_dict']
                self.streamers[streamer] = {
                    'preferred_quality': sd['preferred_quality'],
                    'user_info': sd['user_info'],
                    'active': False,
                }
                self._persist()
                return True, f"Stopped recording '{streamer}'."

            if streamer in self.streamers:
                info = self.streamers[streamer]
                info['active'] = active
                self._persist()
                if not active:
                    return True, f"'{streamer}' set to watch only."
                try:
                    stream_info = twitch.get_stream_info(info['user_info']['id'])
                    if stream_info and stream_info[0].get('type') == 'live':
                        info['stream_info'] = stream_info[0]
                        self._start_watchers([streamer])
                        return True, f"'{streamer}' is live — recording started."
                except Exception as e:
                    log.error('Live check failed: %s', e)
                return True, f"'{streamer}' will be recorded when live."

        return False, f"'{streamer}' not found."

    def remove_streamer(self, streamer):
        streamer = streamer.lower()
        with self._lock:
            if streamer in self.streamers:
                self.streamers.pop(streamer)
                self._persist()
                return True, f"Removed '{streamer}' from watchlist."
            if streamer in self.watched_streamers:
                self.watched_streamers[streamer]['watcher'].quit()
                self._persist()
                return True, f"Stopped and removed '{streamer}'."
        return False, f"'{streamer}' not found. Already removed?"

    def start(self):
        if self.started:
            return 'Daemon is already running.'
        self._check_streams()
        self.started = True
        return 'Daemon started.'

    def set_interval(self, secs):
        self.check_interval = max(1, secs)
        return f'Check interval set to {self.check_interval} seconds.'

    def set_download_folder(self, path):
        self.download_folder = os.path.expanduser(path)
        save_download_folder(self.download_folder)
        return f"Download folder set to '{self.download_folder}'."

    def _persist(self):
        with self._lock:
            entries = {name: info.get('active', False) for name, info in self.streamers.items()}
            for name in self.watched_streamers:
                entries.setdefault(name, True)
        save_streamers([{'name': n, 'active': a} for n, a in entries.items()])

    def get_streamers(self):
        with self._lock:
            return list(self.watched_streamers.keys()), list(self.streamers.keys())

    def get_state(self):
        with self._lock:
            rows = []
            for name, info in self.streamers.items():
                rows.append({
                    'name': name,
                    'active': bool(info.get('active')),
                    'live': info.get('stream_info', {}).get('type') == 'live',
                    'recording': False,
                })
            for name in self.watched_streamers:
                if name in self.streamers:
                    continue  # deactivation in flight; the passive row is already listed
                rows.append({'name': name, 'active': True, 'live': True, 'recording': True})
            return rows

    def exit(self):
        with self._lock:
            self.kill = True
            watchers = [entry['watcher'] for entry in self.watched_streamers.values()]
        for watcher in watchers:
            watcher.quit()
        self.pool.shutdown(wait=False)

        def _stop():
            self.shutdown()
            self.server_close()

        threading.Thread(target=_stop, daemon=True).start()
        return 'Daemon exited.'

    def _check_streams(self):
        try:
            with self._lock:
                user_ids = [info['user_info']['id'] for info in self.streamers.values()]

            if user_ids:
                streams_info = twitch.get_stream_info(*user_ids)
                with self._lock:
                    live_now = set()
                    for stream_info in streams_info:
                        name = stream_info.get('user_login') or stream_info['user_name'].lower()
                        if name in self.streamers:
                            self.streamers[name]['stream_info'] = stream_info
                            live_now.add(name)

                    # drop stale stream_info so offline streamers don't stay marked live
                    for name, info in self.streamers.items():
                        if name not in live_now:
                            info.pop('stream_info', None)

                    live = [
                        name for name, info in self.streamers.items()
                        if info.get('active') and info.get('stream_info', {}).get('type') == 'live'
                    ]
                    self._start_watchers(live)
        except Exception as e:
            log.error('Stream check failed: %s', e)
        finally:
            if not self.kill:
                threading.Timer(self.check_interval, self._check_streams).start()

    def _start_watchers(self, live_streamers):
        # Caller must hold self._lock.
        for name in live_streamers:
            if name not in self.watched_streamers:
                streamer_dict = self.streamers.pop(name)
                watcher = Watcher(streamer_dict, self.download_folder)
                self.watched_streamers[name] = {'watcher': watcher, 'streamer_dict': streamer_dict}
                if not self.kill:
                    self.pool.submit(watcher.watch).add_done_callback(self._watcher_done)

    def _watcher_done(self, future):
        streamer_dict = future.result()
        if not streamer_dict:
            return
        streamer = streamer_dict['user_info']['login']
        with self._lock:
            self.watched_streamers.pop(streamer, None)

        if streamer_dict.get('cleanup'):
            path = streamer_dict.get('output_filepath', '')
            if path and os.path.exists(path):
                os.remove(path)
        else:
            log.info('Finished recording %s.', streamer)

        if not streamer_dict.get('kill'):
            self.add_streamer(streamer, streamer_dict['preferred_quality'], active=True)


if __name__ == '__main__':
    import ATRHandler
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    server = Daemon(('127.0.0.1', 1234), ATRHandler.ATRHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.exit()
    print('Exited.')
