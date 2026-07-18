import datetime
import logging
import os
import shutil
import subprocess
import time

import streamlink

import twitch
from utils import get_valid_filename

log = logging.getLogger(__name__)

CHUNK_SIZE = 256 * 1024      # bytes read per iteration; 1 KB was needlessly CPU-heavy
RECONNECT_DELAY = 5          # seconds to wait before reopening after a dropped stream
MAX_RECONNECTS = 5           # consecutive failed reopen attempts before giving up
FFMPEG_SHUTDOWN_TIMEOUT = 15


def _find_ffmpeg():
    candidates = [shutil.which('ffmpeg')]
    for env_name in ('ProgramFiles', 'ProgramFiles(x86)'):
        root = os.environ.get(env_name)
        if root:
            candidates.append(os.path.join(root, 'Streamlink', 'ffmpeg', 'ffmpeg.exe'))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError('FFmpeg was not found. Install Streamlink or add ffmpeg to PATH.')


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
        self.ffmpeg = _find_ffmpeg()
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

    def _record_part(self, fd, output_filepath):
        command = [
            self.ffmpeg,
            '-hide_banner',
            '-loglevel', 'error',
            '-nostdin',
            '-fflags', '+genpts',
            '-i', 'pipe:0',
            '-map', '0:v:0?',
            '-map', '0:a:0?',
            '-c', 'copy',
            '-avoid_negative_ts', 'make_zero',
            '-cluster_time_limit', '2000',
            '-f', 'matroska',
            '-n',
            output_filepath,
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError:
            fd.close()
            raise
        read_error = None
        stderr_bytes = b''
        try:
            while not self.kill and not self.cleanup:
                data = fd.read(CHUNK_SIZE)
                if not data:
                    break
                process.stdin.write(data)
        except (BrokenPipeError, OSError, streamlink.StreamError) as err:
            read_error = err
        finally:
            try:
                fd.close()
            except Exception:
                pass
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            process.stdin = None
            try:
                _, stderr_bytes = process.communicate(timeout=FFMPEG_SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    _, stderr_bytes = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    _, stderr_bytes = process.communicate()

        return_code = process.returncode
        stderr = stderr_bytes.decode('utf-8', errors='replace').strip()
        if read_error:
            log.warning('Read error for %s: %s', self.streamer, read_error)
        if return_code:
            log.warning('FFmpeg failed for %s with code %d: %s',
                        self.streamer, return_code, stderr or 'unknown error')

        return os.path.exists(output_filepath) and os.path.getsize(output_filepath) > 0

    def watch(self):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')
        filename_stem = f"{timestamp} - {self.streamer} - {get_valid_filename(self.stream_title)}"
        directory = self.download_folder.replace('#streamer#', self.streamer_login)
        os.makedirs(directory, exist_ok=True)
        self.streamer_dict['output_filepaths'] = []

        wrote_any = False
        failures = 0
        part_number = 1
        while not self.kill and not self.cleanup:
            fd = self._open_stream()
            if fd is None:
                if not self._still_live():
                    break  # stream ended
                failures += 1
                if failures > MAX_RECONNECTS:
                    log.warning('Giving up on %s after %d failed reopen attempts.',
                                self.streamer, failures)
                    break
                time.sleep(RECONNECT_DELAY)
                continue

            suffix = '' if part_number == 1 else f' - part {part_number:02d}'
            output_filepath = os.path.join(directory, f'{filename_stem}{suffix}.mkv')
            self.streamer_dict['output_filepath'] = output_filepath
            log.info('%s is live. Recording %s to %s.',
                     self.streamer, self.stream_quality, output_filepath)

            part_written = self._record_part(fd, output_filepath)
            if part_written:
                self.streamer_dict['output_filepaths'].append(output_filepath)
                wrote_any = True
                failures = 0
                part_number += 1
            else:
                failures += 1
                if os.path.exists(output_filepath):
                    os.remove(output_filepath)
                if failures > MAX_RECONNECTS:
                    log.warning('Giving up on %s after %d failed recording attempts.',
                                self.streamer, failures)
                    break

            if self.kill or self.cleanup or not self._still_live():
                break
            log.info('%s stream dropped, starting a new part.', self.streamer)
            time.sleep(RECONNECT_DELAY)

        if not wrote_any:
            self.cleanup = True  # nothing captured; let the daemon delete the empty file

        self.streamer_dict['kill'] = self.kill
        self.streamer_dict['cleanup'] = self.cleanup
        return self.streamer_dict
