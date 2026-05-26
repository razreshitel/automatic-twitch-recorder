import json
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

import requests
from pathvalidate import sanitize_filename

CONFIG_FILE = Path(__file__).parent / 'config.json'

_config = None
_app_access_token = ''
_token_expiry = None


class StreamQualities(Enum):
    AUDIO_ONLY = 'audio_only'
    _160p = '160p'
    _360p = '360p'
    _480p = '480p'
    _720p = '720p'
    _720p60 = '720p60'
    _1080p = '1080p'
    _1080p60 = '1080p60'
    WORST = 'worst'
    BEST = 'best'


def _load_config():
    global _config
    if CONFIG_FILE.exists():
        _config = json.loads(CONFIG_FILE.read_text())
    else:
        _config = {'client_id': '', 'client_secret': '', 'streamers': []}
        CONFIG_FILE.write_text(json.dumps(_config, indent=2))


def _save_config():
    CONFIG_FILE.write_text(json.dumps(_config, indent=2))


def _ensure_config():
    if _config is None:
        _load_config()


def get_client_id():
    _ensure_config()
    if not _config.get('client_id'):
        print('Twitch client ID not set.')
        print('Create an app at https://dev.twitch.tv/console/apps to get credentials.')
        _config['client_id'] = input('Client ID: ').strip()
        _config['client_secret'] = input('Client secret: ').strip()
        _save_config()
    return _config['client_id']


def get_client_secret():
    _ensure_config()
    if not _config.get('client_secret'):
        print('Client secret not set. Visit https://dev.twitch.tv/console/apps.')
        _config['client_secret'] = input('Client secret: ').strip()
        _save_config()
    return _config['client_secret']


def get_app_access_token():
    global _app_access_token, _token_expiry
    if not _app_access_token or not _token_expiry or _token_expiry < datetime.now():
        r = requests.post(
            'https://id.twitch.tv/oauth2/token',
            params={
                'client_id': get_client_id(),
                'client_secret': get_client_secret(),
                'grant_type': 'client_credentials',
            },
        )
        r.raise_for_status()
        data = r.json()
        _app_access_token = data['access_token']
        _token_expiry = datetime.now() + timedelta(seconds=data['expires_in'] - 60)
    return _app_access_token


def get_saved_streamers():
    _ensure_config()
    return list(_config.get('streamers', []))


def save_streamers(names):
    _ensure_config()
    _config['streamers'] = list(names)
    _save_config()


def get_valid_filename(s):
    return sanitize_filename(str(s))
