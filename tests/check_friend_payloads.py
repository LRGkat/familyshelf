"""Exercise actual Steam JSON parsing, including mixed user/clan relationships."""
import contextlib
import io
import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import friend_library as lib

SELF = '76561198000000000'
FRIEND = '76561198000000001'
PRIVATE = '76561198000000002'
EMPTY = '76561198000000003'
TOKEN = 'synthetic.secret.token'
calls = []
priced = []


class Response:
    headers = {}
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps(self.payload).encode()


def upstream(request, **kwargs):
    parsed = urllib.parse.urlsplit(request.full_url)
    params = urllib.parse.parse_qs(parsed.query)
    data = json.loads(params['input_json'][0])
    calls.append((parsed.path, data))
    if 'GetFriendsList' in parsed.path:
        return Response({'response': {'friendslist': {'bincremental': False, 'friends': [
            {'ulfriendid': FRIEND, 'efriendrelationship': 3},
            {'ulfriendid': '103582791429521412', 'efriendrelationship': 3},  # Steam group
            {'ulfriendid': PRIVATE, 'efriendrelationship': 3},
            {'ulfriendid': EMPTY, 'efriendrelationship': '3'},
            {'ulfriendid': '76561198000000004', 'efriendrelationship': 2},  # Pending
            {'ulfriendid': '0', 'efriendrelationship': 3}, None,
            {'ulfriendid': FRIEND, 'efriendrelationship': 3},  # Duplicate
        ]}}})
    if 'GetPlayerLinkDetails' in parsed.path:
        return Response({'response': {'accounts': [
            {'public_data': {'steamid': SELF, 'persona_name': 'Self'}},
            {'public_data': {'steamid': FRIEND, 'persona_name': 'Friend', 'sha_digest_avatar': 'invalid'}},
            {'public_data': None}, {}, None,
        ]}})
    if 'GetRecentlyPlayedGames' in parsed.path:
        if data['steamid'] == PRIVATE: return Response({'response': {}})
        if data['steamid'] == EMPTY: return Response({'response': {'total_count': 0}})
        return Response({'response': {'total_count': 1, 'games': [{'appid': 20, 'name': 'Borrowed game', 'playtime_2weeks': 90, 'playtime_forever': 120}]}})
    if 'GetAchievementsProgress' in parsed.path:
        return Response({'response': {'achievement_progress': [{'appid':a,'total':10,'unlocked':10} for a in data['appids']]}})
    assert 'GetOwnedGames' in parsed.path
    if data['steamid'] == PRIVATE:
        return Response({'response': {}})
    if data['steamid'] == EMPTY:
        return Response({'response': {'game_count': 0}})
    return Response({'response': {'game_count': 1, 'games': [{'appid': 10, 'name': 'Game', 'playtime_forever': 120}]}})


friends = lib.Friends(Path('.'), lambda ids, region, cancel: priced.append(list(ids)))
friends.use_token(TOKEN, SELF)


def finish():
    deadline = time.monotonic() + 10
    while friends.job['running'] and time.monotonic() < deadline:
        time.sleep(.01)
    assert not friends.job['running'], 'job stuck'


with patch.object(lib.urllib.request, 'urlopen', upstream):
    for action in ['list', 'list', 'sync']:
        body = {'action': action}
        if action == 'sync': body['ids'] = [SELF, FRIEND, PRIVATE, EMPTY]
        assert friends.start(body)[0]
        finish()
        assert friends.job['phase'] == 'done', friends.job
        assert [p['steamid'] for p in friends.data['people']] == [SELF, FRIEND, PRIVATE, EMPTY]
        assert [p['status'] for p in friends.data['people']] == ['ok', 'ok', 'unavailable', 'ok']
        assert friends.data['people'][1]['recent_games'][0]['appid'] == 20
        assert friends.data['people'][1]['recent_games'][0]['playtime_2weeks'] == 90
        assert friends.data['people'][2]['recent_status'] == 'unavailable'
        assert friends.data['people'][3]['recent_status'] == 'ok' and friends.data['people'][3]['recent_games'] == []
        assert friends.data['people'][1]['achievement_status'] == 'ok'
        assert {g['appid'] for g in friends.data['people'][1]['achievement_games']} == {10,20}
        assert friends.data['people'][2]['achievement_status'] == 'unavailable'
        assert friends.data['people'][3]['achievement_status'] == 'ok'
        assert priced[-1] == [SELF, FRIEND, PRIVATE, EMPTY]
    assert not any('103582' in str(data) for path, data in calls if 'GetFriendsList' not in path)
    assert TOKEN not in json.dumps(friends.status())

# A malformed library affects only that member; other libraries still synchronize.
def bad_library(request, **kwargs):
    parsed = urllib.parse.urlsplit(request.full_url)
    params = urllib.parse.parse_qs(parsed.query)
    data = json.loads(params['input_json'][0])
    if 'GetOwnedGames' in parsed.path and data['steamid'] == FRIEND:
        return Response({'response': {'game_count': 1, 'games': [{'appid': 10, 'playtime_forever': 'invalid'}]}})
    return upstream(request, **kwargs)

with patch.object(lib.urllib.request, 'urlopen', bad_library):
    assert friends.start({'action': 'list'})[0]
    finish()
    assert friends.job['phase'] == 'done'
    assert friends.data['people'][1]['status'] == 'stale'
    assert friends.data['people'][1]['error_code'] == 'library_format'

# Recent failures retain their own snapshot without breaking library valuation.
def bad_recent(request, **kwargs):
    if 'GetRecentlyPlayedGames' in request.full_url:
        return Response({'response': {'total_count': 1, 'games': [{'appid': 20, 'playtime_2weeks': 'invalid'}]}})
    return upstream(request, **kwargs)
with patch.object(lib.urllib.request, 'urlopen', bad_recent):
    assert friends.start({'action': 'list'})[0]
    finish()
    assert friends.job['phase'] == 'done'
    person = friends.data['people'][1]
    assert person['status'] == 'ok' and person['recent_status'] == 'stale'
    assert person['recent_games'][0]['playtime_2weeks'] == 90
    assert person['recent_error_code'] == 'recent_format'

# Unexpected errors have safe stage diagnostics without credential-bearing messages.
diagnostics = io.StringIO()
with patch.object(lib, 'request_api', side_effect=RuntimeError(TOKEN)), contextlib.redirect_stderr(diagnostics):
    assert friends.start({'action': 'list'})[0]
    finish()
assert friends.job['error_code'] == 'list-RuntimeError'
assert '读取好友列表' in friends.job['error']
assert TOKEN not in json.dumps(friends.status()) + diagnostics.getvalue()
assert 'code=list-RuntimeError' in diagnostics.getvalue()
print('PASS real JSON parsing: mixed clan/user list, incomplete profiles, private/empty libraries, repeated/manual sync and safe diagnostics')
