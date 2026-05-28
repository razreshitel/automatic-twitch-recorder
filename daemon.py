import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import HTTPServer
from pathlib import Path

import twitch
from utils import get_client_id, StreamQualities, get_saved_streamers, save_streamers
from watcher import Watcher

log = logging.getLogger(__name__)

DEFAULT_DOWNLOAD_FOLDER = str(Path.home() / 'streams' / '#streamer#')


class Daemon(HTTPServer):
    check_interval = 30

    def __init__(self, server_address, RequestHandlerClass):
        super().__init__(server_address, RequestHandlerClass)
        self.streamers = {}
        self.watched_streamers = {}
        self.download_folder = DEFAULT_DOWNLOAD_FOLDER
        self.kill = False
        self.started = False
        self.pool = ThreadPoolExecutor()
        self.client_id = get_client_id()
        for name in get_saved_streamers():
            self.add_streamer(name)

    def add_streamer(self, streamer, quality=StreamQualities.BEST.value):
        streamer = streamer.lower()
        valid_qualities = [q.value for q in StreamQualities]
        if quality not in valid_qualities:
            return False, [f"Invalid quality '{quality}'.", f"Options: {valid_qualities}"]

        user_info = twitch.get_user_info(streamer)
        if not user_info:
            return False, [f"Streamer '{streamer}' not found."]

        self.streamers[streamer] = {
            'preferred_quality': quality,
            'user_info': user_info[0],
        }
        self._persist()
        return True, [f"Added '{streamer}' to watchlist."]

    def remove_streamer(self, streamer):
        streamer = streamer.lower()
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
        self.download_folder = path
        return f"Download folder set to '{path}'."

    def _persist(self):
        save_streamers(list(self.streamers.keys()) + list(self.watched_streamers.keys()))

    def get_streamers(self):
        return list(self.watched_streamers.keys()), list(self.streamers.keys())

    def exit(self):
        self.kill = True
        for entry in self.watched_streamers.values():
            entry['watcher'].quit()
        self.pool.shutdown(wait=False)
        self.server_close()
        threading.Thread(target=self.shutdown, daemon=True).start()
        return 'Daemon exited.'

    def _check_streams(self):
        user_ids = [info['user_info']['id'] for info in self.streamers.values()]

        if user_ids:
            try:
                streams_info = twitch.get_stream_info(*user_ids)
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
                    if info.get('stream_info', {}).get('type') == 'live'
                ]
                self._start_watchers(live)
            except Exception as e:
                log.error(f'Stream check failed: {e}')

        if not self.kill:
            threading.Timer(self.check_interval, self._check_streams).start()

    def _start_watchers(self, live_streamers):
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
        self.watched_streamers.pop(streamer, None)

        if streamer_dict.get('cleanup'):
            path = streamer_dict.get('output_filepath', '')
            if path and os.path.exists(path):
                os.remove(path)
        else:
            log.info(f'Finished recording {streamer}.')

        if not streamer_dict.get('kill'):
            self.add_streamer(streamer, streamer_dict['preferred_quality'])


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
