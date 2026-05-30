import datetime
import logging
import os
import time

import streamlink

import twitch
from utils import get_valid_filename

log = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024      # bytes read per iteration; 1 KB was needlessly CPU-heavy
RECONNECT_DELAY = 5          # seconds to wait before reopening after a dropped stream
MAX_RECONNECTS = 5           # consecutive failed reopen attempts before giving up


class Watcher:
    def __init__(self, streamer_dict, download_folder):
        self.streamer_dict = streamer_dict
        self.streamer = streamer_dict['user_info']['display_name']
        self.streamer_login = streamer_dict['user_info']['login']
        self.user_id = streamer_dict['user_info']['id']
        self.stream_title = streamer_dict['stream_info']['title']
        self.stream_quality = streamer_dict['preferred_quality']
        self.url = f'https://www.twitch.tv/{self.streamer_login}'
        self.download_folder = download_folder
        self.kill = False
        self.cleanup = False

    def quit(self):
        self.kill = True

    def clean_break(self):
        self.cleanup = True

    def _open_stream(self):
        """Resolve qualities and open the stream, returning an open fd or None.

        Quality fallback is per-session only; the user's preferred_quality is
        left untouched so the next recording tries it again.
        """
        try:
            streams = streamlink.streams(self.url)
        except Exception as err:
            log.warning('Could not list streams for %s: %s', self.streamer, err)
            return None
        if not streams:
            return None

        quality = self.stream_quality
        if quality not in streams:
            quality = 'best' if 'best' in streams else list(streams.keys())[-1]
            log.info("Quality '%s' unavailable for %s, using '%s'.",
                     self.stream_quality, self.streamer, quality)
        try:
            return streams[quality].open()
        except Exception as err:
            log.warning('Failed to open stream for %s: %s', self.streamer, err)
            return None

    def _still_live(self):
        try:
            info = twitch.get_stream_info(self.user_id)
            return bool(info) and info[0].get('type') == 'live'
        except Exception as err:
            log.warning('Live check failed for %s: %s', self.streamer, err)
            return True  # be optimistic; the next open attempt decides

    def watch(self):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')
        filename = f"{timestamp} - {self.streamer} - {get_valid_filename(self.stream_title)}.ts"
        directory = self.download_folder.replace('#streamer#', self.streamer_login)
        os.makedirs(directory, exist_ok=True)
        output_filepath = os.path.join(directory, filename)
        self.streamer_dict['output_filepath'] = output_filepath

        log.info('%s is live. Recording %s to %s.', self.streamer, self.stream_quality, output_filepath)

        wrote_any = False
        failures = 0
        with open(output_filepath, 'ab') as out_file:
            while not self.kill and not self.cleanup:
                fd = self._open_stream()
                if fd is None:
                    if not self._still_live():
                        break  # stream ended for real
                    failures += 1
                    if failures > MAX_RECONNECTS:
                        log.warning('Giving up on %s after %d failed reopen attempts.',
                                    self.streamer, failures)
                        break
                    time.sleep(RECONNECT_DELAY)
                    continue

                failures = 0
                try:
                    while not self.kill and not self.cleanup:
                        data = fd.read(CHUNK_SIZE)
                        if not data:
                            break  # stream dropped or ended
                        out_file.write(data)
                        wrote_any = True
                except (OSError, streamlink.StreamError) as err:
                    log.warning('Read error for %s: %s', self.streamer, err)
                finally:
                    try:
                        fd.close()
                    except Exception:
                        pass

                if self.kill or self.cleanup or not self._still_live():
                    break
                # Brief drop while still live — reopen and keep appending to the same file.
                log.info('%s stream dropped, reconnecting…', self.streamer)
                time.sleep(RECONNECT_DELAY)

        if not wrote_any:
            self.cleanup = True  # nothing captured; let the daemon delete the empty file

        self.streamer_dict['kill'] = self.kill
        self.streamer_dict['cleanup'] = self.cleanup
        return self.streamer_dict
