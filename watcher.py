import datetime
import os

import streamlink

from utils import get_valid_filename


class Watcher:
    def __init__(self, streamer_dict, download_folder):
        self.streamer_dict = streamer_dict
        self.streamer = streamer_dict['user_info']['display_name']
        self.streamer_login = streamer_dict['user_info']['login']
        self.stream_title = streamer_dict['stream_info']['title']
        self.stream_quality = streamer_dict['preferred_quality']
        self.download_folder = download_folder
        self.kill = False
        self.cleanup = False

    def quit(self):
        self.kill = True

    def clean_break(self):
        self.cleanup = True

    def watch(self):
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H.%M.%S')
        filename = f"{timestamp} - {self.streamer} - {get_valid_filename(self.stream_title)}.ts"
        directory = self.download_folder.replace('#streamer#', self.streamer_login)
        os.makedirs(directory, exist_ok=True)
        output_filepath = os.path.join(directory, filename)
        self.streamer_dict['output_filepath'] = output_filepath

        streams = streamlink.streams(f'https://www.twitch.tv/{self.streamer_login}')

        try:
            stream = streams[self.stream_quality]
        except KeyError:
            if not streams:
                self.cleanup = True
                return self.streamer_dict

            fallback = self.stream_quality if self.stream_quality in streams else list(streams.keys())[-1]
            print(f"Quality '{self.stream_quality}' unavailable, falling back to '{fallback}'.")
            self.stream_quality = fallback
            self.streamer_dict['preferred_quality'] = fallback
            stream = streams[self.stream_quality]

        if self.kill or self.cleanup or not stream:
            return self.streamer_dict

        print(f'{self.streamer} is live. Recording {self.stream_quality} to {output_filepath}.')

        try:
            with open(output_filepath, 'ab') as out_file:
                fd = stream.open()
                while not self.kill and not self.cleanup:
                    data = fd.read(1024)
                    if not data:
                        fd.close()
                        break
                    out_file.write(data)
        except streamlink.StreamError as err:
            print(f'StreamError: {err}')
        except IOError as err:
            print(f'Failed to write to file: {err}')

        self.streamer_dict['kill'] = self.kill
        self.streamer_dict['cleanup'] = self.cleanup
        return self.streamer_dict
