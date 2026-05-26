import requests
import utils


def _auth():
    return {
        'Client-ID': utils.get_client_id(),
        'Authorization': 'Bearer ' + utils.get_app_access_token(),
    }


def get_user_info(user_login, *args):
    logins = [user_login] + list(args[:99])
    r = requests.get(
        'https://api.twitch.tv/helix/users',
        headers=_auth(),
        params=[('login', l) for l in logins],
    )
    r.raise_for_status()
    return r.json().get('data', [])


def get_stream_info(user_id, *args):
    ids = [user_id] + list(args[:99])
    r = requests.get(
        'https://api.twitch.tv/helix/streams',
        headers=_auth(),
        params=[('user_id', i) for i in ids] + [('first', '100')],
    )
    r.raise_for_status()
    return r.json().get('data', [])
