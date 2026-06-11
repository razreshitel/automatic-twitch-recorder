import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from http.server import ThreadingHTTPServer
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

DEFAULT_DOWNLOAD_FOLDER = str(Path(os.environ.get('SystemDrive', 'C:') + os.sep) / 'streams' / '#streamer#')
SHUTDOWN_DRAIN_TIMEOUT = 10  # seconds to let recordings flush and close on exit


class Daemon(ThreadingHTTPServer):
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
        self._load_saved_streamers()

    def _load_saved_streamers(self):
        entries = get_saved_streamers()
        names = [e['name'] for e in entries]
        # One helix/users request covers up to 100 logins; the old per-streamer
        # loop fired one request each on startup.
        info_map = {}
        if names:
            try:
                for info in twitch.get_user_info(*names):
                    info_map[info['login'].lower()] = info
            except Exception as e:
                log.error('Startup user lookup failed: %s', e)
        for e in entries:
            # Recording selection is deliberately not restored: the app always
            # starts with an empty recording list, everything in the watchlist.
            self.add_streamer(e['name'], active=False,
                              user_info=info_map.get(e['name'].lower()))

    def add_streamer(self, streamer, quality=StreamQualities.BEST.value, active=False,
                     user_info=None, check_live=False):
        streamer = streamer.lower()
        valid_qualities = [q.value for q in StreamQualities]
        if quality not in valid_qualities:
            return False, [f"Invalid quality '{quality}'.", f"Options: {valid_qualities}"]

        if user_info is None:
            found = twitch.get_user_info(streamer)
            if not found:
                return False, [f"Streamer '{streamer}' not found."]
            user_info = found[0]

        with self._lock:
            self.streamers[streamer] = {
                'preferred_quality': quality,
                'user_info': user_info,
                'active': active,
            }
            self._persist()

        if check_live:
            live = self._refresh_live(streamer)
            return True, [f"Added '{streamer}' to watchlist ({'live now' if live else 'offline'})."]
        return True, [f"Added '{streamer}' to watchlist."]

    def _refresh_live(self, streamer):
        """Query Twitch for one streamer's current status and update its
        stream_info. Returns True if live. Network call is made outside the lock."""
        with self._lock:
            info = self.streamers.get(streamer)
            if not info:
                return False
            user_id = info['user_info']['id']
        try:
            stream_info = twitch.get_stream_info(user_id)
        except Exception as e:
            log.error('Live check failed for %s: %s', streamer, e)
            return False
        live = bool(stream_info) and stream_info[0].get('type') == 'live'
        with self._lock:
            info = self.streamers.get(streamer)
            if info is not None:
                if live:
                    info['stream_info'] = stream_info[0]
                else:
                    info.pop('stream_info', None)
        return live

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
            futures = [entry['future'] for entry in self.watched_streamers.values()
                       if entry.get('future')]
        for watcher in watchers:
            watcher.quit()
        # Let active recordings notice the quit flag, flush their buffer, and
        # close the file cleanly rather than dying mid-write. Bounded so a
        # wedged stream cannot hang shutdown.
        if futures:
            wait(futures, timeout=SHUTDOWN_DRAIN_TIMEOUT)
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
                entry = {'watcher': watcher, 'streamer_dict': streamer_dict, 'future': None}
                self.watched_streamers[name] = entry
                if not self.kill:
                    fut = self.pool.submit(watcher.watch)
                    fut.add_done_callback(lambda f, n=name: self._watcher_done(n, f))
                    entry['future'] = fut

    def _watcher_done(self, name, future):
        try:
            streamer_dict = future.result()
        except Exception as e:
            # A watcher that raised would otherwise leave name stuck in
            # watched_streamers forever (shown as "recording", never retried).
            log.exception('Recording for %s crashed: %s', name, e)
            streamer_dict = None

        with self._lock:
            self.watched_streamers.pop(name, None)

        if streamer_dict is None:
            if not self.kill:
                self.add_streamer(name, active=True)  # keep watching; retry when live again
            return

        if streamer_dict.get('cleanup'):
            path = streamer_dict.get('output_filepath', '')
            if path and os.path.exists(path):
                os.remove(path)
        else:
            log.info('Finished recording %s.', name)

        if not streamer_dict.get('kill'):
            self.add_streamer(name, streamer_dict['preferred_quality'], active=True)


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
