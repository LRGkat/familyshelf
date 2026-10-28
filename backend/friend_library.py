"""Steam public friends/libraries; Credentials stay in process memory."""
import copy
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import steam_family_library as steam


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def steam_id(value):
    value = str(value or '').strip()
    match = re.fullmatch(r'(?:https://steamcommunity\.com/profiles/)?(7656119\d{10})/?', value)
    if not match:
        raise ValueError('请输入 17 位 SteamID64，或含数字 ID 的 Steam 个人主页地址。')
    return match.group(1)


class FriendError(Exception):
    pass


def request_api(method, key, **params):
    params['access_token' if '.' in key else 'key'] = key
    req = urllib.request.Request(f'{steam.API}/{method}/?' + urllib.parse.urlencode(params),
                                 headers={'User-Agent': 'FamilyShelf/2.0'})
    try:
        with urllib.request.urlopen(req, timeout=22, context=steam.api_tls_context()) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise FriendError('Steam 请求限流，请稍后重试。') from None
        if error.code in (401, 403):
            raise FriendError('Steam 未授权读取此数据：可补充 API Key，并检查好友列表 / 游戏详情的隐私设置；无需重复登录。') from None
        raise FriendError(f'Steam 接口暂不可用（HTTP {error.code}）。') from None
    except FriendError:
        raise
    except Exception:
        # Do not expose a credential-bearing URL or upstream exception.
        raise FriendError('无法读取 Steam 数据，请检查网络后重试。') from None


def profiles(key, ids):
    result = []
    for offset in range(0, len(ids), 100):
        payload = request_api('ISteamUser/GetPlayerSummaries/v2', key,
                              steamids=','.join(ids[offset:offset + 100]))
        result.extend(payload.get('response', {}).get('players', []))
    return {str(p['steamid']): {k: p[k] for k in
            ('steamid', 'personaname', 'avatarfull', 'personastate', 'lastlogoff', 'profileurl') if k in p}
            for p in result if isinstance(p, dict) and p.get('steamid') in ids}


def owned_games(key, sid):
    payload = request_api('IPlayerService/GetOwnedGames/v1', key, steamid=sid,
                          include_appinfo='true', include_played_free_games='true')
    response = payload.get('response')
    if not isinstance(response, dict) or 'game_count' not in response:
        raise FriendError('游戏库不可见，可能为私密资料或接口暂未返回。')
    count = response['game_count']
    games = response.get('games', [])
    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or not isinstance(games, list):
        raise FriendError('Steam 返回的游戏库格式无效，保留上次快照。')
    if count != len(games):
        raise FriendError('Steam 返回的游戏库不完整，保留上次快照。')
    clean, seen = [], set()
    for game in games:
        if not isinstance(game, dict):
            raise FriendError('游戏条目无效。')
        appid = game.get('appid')
        if not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0 or appid in seen:
            raise FriendError('游戏编号无效或重复。')
        seen.add(appid)
        clean.append({'appid': appid, 'name': str(game.get('name') or f'App {appid}')[:300],
                      'playtime_forever': max(0, int(game.get('playtime_forever') or 0)),
                      'playtime_2weeks': max(0, int(game.get('playtime_2weeks') or 0)),
                      'rtime_last_played': max(0, int(game.get('rtime_last_played') or 0))})
    return clean


class Friends:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.key = None
        self.access_token = None
        self.cancel = threading.Event()
        self.revision = 0
        self.job = {'running': False, 'done': 0, 'total': 0, 'message': '连接 Steam API，读取好友的公开收藏。'}
        self.data = {'account_id': '', 'fetched_at': None, 'people': []}

    def status(self):
        with self.lock:
            return {'data': copy.deepcopy(self.data), 'revision': self.revision,
                    'key_configured': bool(self.key), 'auth_configured': bool(self.key or self.access_token), 'job': dict(self.job)}

    def use_token(self, token, account):
        with self.lock:
            if self.data['account_id'] not in ('', account):
                self.cancel.set()
                self.key = None
                self.data = {'account_id': account, 'fetched_at': None, 'people': []}
                self.save()
            self.access_token = token

    def save(self):
        self.revision += 1

    def start(self, body):
        action = body.get('action', 'list')
        if action not in ('list', 'sync', 'add'):
            return False, '未知的好友操作。'
        with self.lock:
            if self.job['running']:
                return False, '已有好友同步任务，请等待完成或先取消。'
            key = body.get('key') or self.key or self.access_token
            if not isinstance(key, str) or not ((bool(self.access_token) and key == self.access_token) or re.fullmatch(r'[a-fA-F0-9]{32}', key.strip())):
                return False, '请先设置有效的 Steam Web API Key（32 位十六进制字符）。'
            try:
                account = steam_id(body.get('steamid') or self.data.get('account_id'))
                ids = body.get('ids', [])
                if not isinstance(ids, list) or len(ids) > 100:
                    raise ValueError('每次最多同步 100 位好友。')
                ids = list(dict.fromkeys(steam_id(x) for x in ids))
                if action == 'sync':
                    existing = {p['steamid'] for p in self.data['people']}
                    if account != self.data.get('account_id') or not ids or any(s not in existing for s in ids):
                        raise ValueError('请先加载当前账号的好友，再勾选需要同步的成员。')
                if action == 'add' and (not ids or len(ids) > 10):
                    raise ValueError('每次手动添加 1–10 个 SteamID。')
                if action == 'add' and self.data.get('account_id') not in ('', account):
                    raise ValueError('请先为新账号加载好友列表，再手动添加。')
            except ValueError as error:
                return False, str(error)
            if '.' not in key:
                self.key = key.strip()
            self.cancel = threading.Event()
            self.job = {'running': True, 'done': 0, 'total': len(ids), 'message': '正在连接 Steam…'}
            threading.Thread(target=self.run, args=(action, account, ids, key.strip(), self.cancel), daemon=True).start()
        return True, '正在读取公开数据，您可以继续浏览。'

    def run(self, action, account, ids, key, cancel):
        try:
            if action == 'list':
                payload = request_api('ISteamUser/GetFriendList/v1', key, steamid=account, relationship='friend')
                items = payload.get('friendslist', {}).get('friends')
                if not isinstance(items, list):
                    raise FriendError('好友列表不可见，请检查隐私设置，或手动添加 SteamID。')
                ids = list(dict.fromkeys([account] + [steam_id(p.get('steamid')) for p in items]))[:2001]
                info = profiles(key, ids)
                with self.lock:
                    if cancel.is_set():
                        return
                    old = {p['steamid']: p for p in self.data['people']} if self.data.get('account_id') == account else {}
                    self.data = {'account_id': account, 'fetched_at': timestamp(), 'people': [
                        {**old.get(sid, {'steamid': sid, 'games': [], 'status': 'pending'}),
                         **info.get(sid, {}), 'is_self': sid == account,
                         'friend_since': next((p.get('friend_since') for p in items if str(p.get('steamid')) == sid), None)}
                        for sid in ids]}
                    self.save()
                    self.job.update(done=len(ids), total=len(ids), message=f'已加载 {len(ids)-1} 位好友，勾选后同步游戏库。')
                return
            if action == 'add':
                info = profiles(key, ids)
                with self.lock:
                    if cancel.is_set():
                        return
                    self.data['account_id'] = account
                    existing = {p['steamid'] for p in self.data['people']}
                    for sid in ids:
                        if sid not in existing:
                            self.data['people'].append({'steamid': sid, **info.get(sid, {}),
                                                        'games': [], 'status': 'pending', 'is_self': sid == account, 'manual': True})
                    self.save()
            failures = 0
            for index, sid in enumerate(ids):
                if cancel.is_set():
                    break
                with self.lock:
                    self.job['message'] = f'正在同步游戏库 {index+1} / {len(ids)}…'
                try:
                    games = owned_games(key, sid)
                    update = {'games': games, 'status': 'ok', 'error': '', 'fetched_at': timestamp()}
                except FriendError as error:
                    failures += 1
                    update = {'error': str(error)}
                with self.lock:
                    if cancel.is_set():
                        break
                    person = next(p for p in self.data['people'] if p['steamid'] == sid)
                    if 'games' not in update:
                        update['status'] = 'stale' if person.get('fetched_at') else 'unavailable'
                    person.update(update)
                    self.job['done'] = index + 1
                    self.data['fetched_at'] = timestamp()
                    self.save()
                if '限流' in update.get('error', ''):
                    with self.lock:
                        self.job['message'] = 'Steam 限流，已保存进度，请稍后继续同步。'
                    return
                if index + 1 < len(ids) and cancel.wait(.35):
                    break
            with self.lock:
                self.job['message'] = f'同步完成：{self.job["done"]} 位，{failures} 位暂不可读；私密库不按零元处理。'
        except FriendError as error:
            with self.lock:
                if cancel is self.cancel and not cancel.is_set():
                    self.job['message'] = str(error)
        except Exception:
            with self.lock:
                if cancel is self.cancel and not cancel.is_set():
                    self.job['message'] = '同步未完成，已保留上次数据。请稍后重试。'
        finally:
            with self.lock:
                if cancel is self.cancel:
                    self.job['running'] = False
                    if cancel.is_set():
                        self.job['message'] = '已取消好友同步，保留已完成的快照。'

    def disconnect(self):
        with self.lock:
            self.cancel.set()
            self.key = None
            self.access_token = None
            self.job['message'] = '已清除内存中的 API Key；本地好友快照仍可浏览。'
