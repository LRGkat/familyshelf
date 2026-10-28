"""Steam public friends/libraries; Credentials stay in process memory."""
import copy
import base64
import json
import re
import sys
import threading
import traceback
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


def friend_entries(payload, token_auth):
    """ClientFriendsList also contains clan relationships, not just people."""
    envelope = payload.get('response') if token_auth else payload
    friendslist = envelope.get('friendslist') if isinstance(envelope, dict) else None
    if not isinstance(friendslist, dict):
        raise FriendError('好友列表不可见或令牌权限不足；请补充 API Key 或检查隐私设置，无需再次登录。', 'permission')
    entries = friendslist.get('friends', [])
    if not isinstance(entries, list):
        raise FriendError('Steam 返回的好友列表格式无效，请稍后重试。', 'list_format')
    people = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if token_auth and str(entry.get('efriendrelationship')) != '3':
            continue
        try:
            sid = steam_id(entry.get('ulfriendid' if token_auth else 'steamid'))
        except ValueError:
            # Groups, pending/removed accounts and invalid records are not users.
            continue
        people.append({'steamid': sid, 'friend_since': entry.get('friend_since')})
    return people


class FriendError(Exception):
    def __init__(self, message, code='upstream'):
        super().__init__(message)
        self.code = code


def request_api(method, key, **params):
    if ('.' in key and method.startswith(('IPlayerService/', 'IFriendsListService/'))
            or method == 'IPlayerService/GetAchievementsProgress/v1'):
        params = {'input_json': json.dumps(params)}
    else:
        params = {k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()}
    params['access_token' if '.' in key else 'key'] = key
    encoded = urllib.parse.urlencode(params)
    if method == 'IPlayerService/GetAchievementsProgress/v1':
        # Steam explicitly returns 405 Allow: POST for this bulk endpoint.
        req = urllib.request.Request(f'{steam.API}/{method}/', data=encoded.encode('utf-8'),
                                     headers={'User-Agent': 'FamilyShelf/2.0',
                                              'Content-Type': 'application/x-www-form-urlencoded'},
                                     method='POST')
    else:
        req = urllib.request.Request(f'{steam.API}/{method}/?' + encoded,
                                     headers={'User-Agent': 'FamilyShelf/2.0'})
    try:
        with urllib.request.urlopen(req, timeout=22, context=steam.api_tls_context()) as response:
            eresult = response.headers.get('x-eresult', '1')
            if eresult != '1':
                if eresult in ('5', '15', '27'):
                    raise FriendError('Steam 拒绝好友接口权限；可补充 API Key，无需再次登录。', 'permission')
                raise FriendError('Steam 暂未返回好友数据，请稍后重试。')
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise FriendError('Steam 请求限流，请稍后重试。', 'rate_limit') from None
        if error.code in (401, 403):
            raise FriendError('Steam 未授权读取此数据：可补充 API Key，并检查好友列表 / 游戏详情的隐私设置；无需重复登录。', 'permission') from None
        raise FriendError(f'Steam 接口暂不可用（HTTP {error.code}）。') from None
    except FriendError:
        raise
    except Exception:
        # Do not expose a credential-bearing URL or upstream exception.
        raise FriendError('无法读取 Steam 数据，请检查网络后重试。') from None


def profiles(key, ids):
    result = []
    for offset in range(0, len(ids), 100):
        batch = ids[offset:offset + 100]
        try:
            if '.' in key:
                payload = request_api('IPlayerService/GetPlayerLinkDetails/v1', key, steamids=batch)
                response = payload.get('response') or {}
                accounts = (response.get('accounts') or []) if isinstance(response, dict) else []
                for account in accounts if isinstance(accounts, list) else []:
                    public = account.get('public_data') if isinstance(account, dict) else None
                    if not isinstance(public, dict):
                        continue
                    person = {'steamid': str(public.get('steamid', '')), 'personaname': public.get('persona_name', '')}
                    digest = public.get('sha_digest_avatar')
                    if digest:
                        try:
                            digest = base64.b64decode(digest, validate=True).hex()
                            if len(digest) == 40:
                                person['avatarfull'] = f'https://avatars.steamstatic.com/{digest}_full.jpg'
                        except (ValueError, TypeError):
                            pass
                    result.append(person)
            else:
                payload = request_api('ISteamUser/GetPlayerSummaries/v2', key, steamids=','.join(batch))
                response = payload.get('response') or {}
                players = response.get('players') if isinstance(response, dict) else None
                if isinstance(players, list):
                    result.extend(players)
        except FriendError as error:
            if error.code == 'rate_limit':
                raise
            # A missing avatar/profile must not block every game library.
    return {str(p['steamid']): {k: p[k] for k in
            ('steamid', 'personaname', 'avatarfull', 'personastate', 'lastlogoff', 'profileurl') if k in p}
            for p in result if isinstance(p, dict) and p.get('steamid') in ids}


def owned_games(key, sid):
    payload = request_api('IPlayerService/GetOwnedGames/v1', key, steamid=sid,
                          include_appinfo=True, include_played_free_games=True)
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
        try:
            clean.append({'appid': appid, 'name': str(game.get('name') or f'App {appid}')[:300],
                          'playtime_forever': max(0, int(game.get('playtime_forever') or 0)),
                          'playtime_2weeks': max(0, int(game.get('playtime_2weeks') or 0)),
                          'rtime_last_played': max(0, int(game.get('rtime_last_played') or 0))})
        except (ValueError, TypeError, OverflowError):
            raise FriendError('Steam 返回的游戏时长格式无效，保留上次快照。', 'library_format') from None
    return clean


def recently_played(key, sid):
    payload = request_api('IPlayerService/GetRecentlyPlayedGames/v1', key, steamid=sid, count=0)
    response = payload.get('response')
    if not isinstance(response, dict) or 'total_count' not in response:
        raise FriendError('最近游玩详情不可见或 Steam 暂未返回，请检查游戏详情隐私设置。', 'recent_unavailable')
    count, games = response['total_count'], response.get('games', [])
    if (not isinstance(count, int) or isinstance(count, bool) or count < 0
            or not isinstance(games, list) or count != len(games)):
        raise FriendError('最近游玩数据不完整，保留上次快照。', 'recent_format')
    result, seen = [], set()
    for game in games:
        if not isinstance(game, dict):
            raise FriendError('最近游玩条目格式无效。', 'recent_format')
        appid = game.get('appid')
        if not isinstance(appid, int) or isinstance(appid, bool) or appid <= 0 or appid in seen:
            raise FriendError('最近游玩游戏编号无效。', 'recent_format')
        seen.add(appid)
        try:
            minutes = int(game['playtime_2weeks'])
            total = int(game.get('playtime_forever') or 0)
            if minutes < 0 or total < 0:
                raise ValueError()
        except (KeyError, ValueError, TypeError, OverflowError):
            raise FriendError('最近游玩时长格式无效。', 'recent_format') from None
        result.append({'appid': appid, 'name': str(game.get('name') or f'App {appid}')[:300],
                       'playtime_2weeks': minutes, 'playtime_forever': total})
    return result


def achievement_progress(key, sid, games, cancel, on_progress=None):
    """Read bulk progress; omitted apps remain unknown, never zero completions."""
    catalog = {g['appid']: g for g in games}
    ids = list(catalog)
    found = {}
    for offset in range(0, len(ids), 100):
        if cancel.is_set():
            return None
        batch = ids[offset:offset + 100]
        payload = request_api('IPlayerService/GetAchievementsProgress/v1', key,
                              steamid=sid, appids=batch, language='schinese', include_unvetted_apps=True)
        response = payload.get('response')
        entries = response.get('achievement_progress') if isinstance(response, dict) else None
        if not isinstance(entries, list):
            raise FriendError('Steam 未返回成就进度，请检查游戏详情隐私或当前认证的成就接口权限。', 'achievement_unavailable')
        for entry in entries:
            if not isinstance(entry, dict):
                raise FriendError('Steam 成就数据格式无效，已保留上次快照。', 'achievement_format')
            appid, total, unlocked = entry.get('appid'), entry.get('total', 0), entry.get('unlocked', 0)
            if (not isinstance(appid, int) or isinstance(appid, bool) or appid not in batch or appid in found
                    or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in (total, unlocked))
                    or unlocked > total):
                raise FriendError('Steam 成就数量无效，已保留上次快照。', 'achievement_format')
            found[appid] = {'appid': appid, 'name': catalog[appid]['name'], 'total': total,
                            'unlocked': unlocked, 'complete': total > 0 and unlocked == total,
                            'playtime_forever': catalog[appid].get('playtime_forever', 0)}
        if on_progress:
            on_progress(min(offset + len(batch), len(ids)), len(ids))
        if offset + len(batch) < len(ids) and cancel.wait(.2):
            return None
    unknown = len(ids) - len(found)
    return {'achievement_games': list(found.values()), 'achievement_scope': len(ids),
            'achievement_checked': len(found), 'achievement_unknown': unknown,
            'achievement_status': 'partial' if unknown else 'ok',
            'achievement_error': f'{unknown} 款游戏未返回成就数据，已确认结果可查看，暂不参与完整排名。' if unknown else '',
            'achievement_error_code': 'achievement_incomplete' if unknown else '',
            'achievement_fetched_at': timestamp()}


class Friends:
    def __init__(self, root, on_synced=None):
        self.root = root
        self.lock = threading.RLock()
        self.key = None
        self.access_token = None
        self.cancel = threading.Event()
        self.revision = 0
        self.job = {'running': False, 'done': 0, 'total': 0, 'message': '连接 Steam API，读取好友的公开收藏。'}
        self.data = {'account_id': '', 'fetched_at': None, 'people': []}
        self.on_synced = on_synced

    def status(self):
        with self.lock:
            return {'data': copy.deepcopy(self.data), 'revision': self.revision,
                    'key_configured': bool(self.key), 'auth_configured': bool(self.key or self.access_token),
                    'managed_sync': True, 'job': dict(self.job)}

    def use_token(self, token, account):
        with self.lock:
            if self.data['account_id'] not in ('', account):
                self.cancel.set()
                self.key = None
                self.data = {'account_id': account, 'fetched_at': None, 'people': []}
                self.save()
            self.access_token = token
            self.data['account_id'] = account

    def save(self):
        self.revision += 1

    def start(self, body):
        action = body.get('action', 'list')
        if action not in ('list', 'sync', 'add', 'prices'):
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
                if not isinstance(ids, list) or len(ids) > 2001:
                    raise ValueError('每次最多同步 2001 位成员。')
                ids = list(dict.fromkeys(steam_id(x) for x in ids))
                if action in ('sync', 'prices'):
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
            region = body.get('region', 'cn')
            if region not in ('cn', 'us', 'hk'):
                return False, '未知的估值地区。'
            self.job = {'running': True, 'phase': 'list' if action == 'list' else 'sync', 'done': 0, 'total': len(ids), 'error': '', 'error_code': '', 'message': '正在读取好友列表…' if action == 'list' else '正在同步游戏库…'}
            threading.Thread(target=self.run, args=(action, account, ids, key.strip(), self.cancel, region), daemon=True).start()
        return True, '正在读取公开数据，您可以继续浏览。'

    def run(self, action, account, ids, key, cancel, region='cn'):
        stage = 'list' if action == 'list' else 'profiles' if action == 'add' else 'libraries'
        try:
            if action == 'list':
                if '.' in key:
                    payload = request_api('IFriendsListService/GetFriendsList/v1', key)
                else:
                    payload = request_api('ISteamUser/GetFriendList/v1', key, steamid=account, relationship='friend')
                items = friend_entries(payload, '.' in key)
                ids = list(dict.fromkeys([account] + [steam_id(p.get('steamid')) for p in items]))[:2001]
                stage = 'profiles'
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
                    self.job.update(done=0, total=len(ids), phase='sync', message=f'已加载 {len(ids)-1} 位好友，正在自动同步全部游戏库…')
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
            stage = 'libraries'
            for index, sid in enumerate(ids if action != 'prices' else []):
                if cancel.is_set():
                    break
                with self.lock:
                    self.job['message'] = f'正在同步游戏库 {index+1} / {len(ids)}…'
                try:
                    games = owned_games(key, sid)
                    update = {'games': games, 'status': 'ok', 'error': '', 'error_code': '', 'fetched_at': timestamp()}
                except FriendError as error:
                    failures += 1
                    update = {'error': str(error), 'error_code': error.code}
                if cancel.is_set():
                    break
                # Recent activity has independent privacy and availability from owned games.
                # Keep it separate so borrowed games can appear without inflating library value.
                try:
                    recent = recently_played(key, sid)
                    update.update(recent_games=recent, recent_status='ok', recent_error='',
                                  recent_error_code='', recent_fetched_at=timestamp())
                except FriendError as error:
                    update.update(recent_status='unavailable', recent_error=str(error), recent_error_code=error.code)
                with self.lock:
                    if cancel.is_set():
                        break
                    person = next(p for p in self.data['people'] if p['steamid'] == sid)
                    if 'games' not in update:
                        update['status'] = 'stale' if person.get('fetched_at') else 'unavailable'
                    if update['recent_status'] != 'ok' and person.get('recent_fetched_at'):
                        update['recent_status'] = 'stale'
                    person.update(update)
                    self.job['done'] = index + 1
                    self.data['fetched_at'] = timestamp()
                    self.save()
                if update.get('error_code') == 'rate_limit' or (update.get('error_code') == 'permission' and sid == account):
                    raise FriendError(update['error'], update['error_code'])
                if index + 1 < len(ids) and cancel.wait(.35):
                    break
            if not cancel.is_set() and self.on_synced:
                stage = 'pricing'
                self.on_synced(ids, region, cancel)
            # Run after libraries and prices so a slow/private achievement service
            # cannot prevent the existing boards from receiving their data.
            if not cancel.is_set() and action != 'prices':
                stage = 'achievements'
                self.sync_achievements(ids, self.access_token or key, cancel)
            with self.lock:
                if cancel is self.cancel and not cancel.is_set():
                    achievement_ready = sum(p.get('achievement_status') == 'ok'
                                            for p in self.data['people'] if p['steamid'] in ids)
                    suffix = f'成就完整读取 {achievement_ready} / {len(ids)} 位，未完整记录单独标记。' if action != 'prices' else '未知价格单独标记。'
                    self.job.update(phase='done', message=f'同步与估值完成：{len(ids)} 位，{failures} 位游戏库暂不可读；{suffix}')
        except FriendError as error:
            with self.lock:
                if cancel is self.cancel and not cancel.is_set():
                    self.job.update(phase='error', message=str(error), error=str(error), error_code=error.code)
        except Exception as error:
            # Never log exception messages/locals: they can contain credential URLs.
            trace = traceback.extract_tb(error.__traceback__)
            frames = ','.join(f'{frame.name}:{frame.lineno}' for frame in trace
                              if frame.filename == __file__)
            diagnostic = f'{stage}-{type(error).__name__}'
            print(f'friends_sync_failure code={diagnostic} frames={frames}', file=sys.stderr, flush=True)
            label = {'list':'读取好友列表', 'profiles':'读取好友资料', 'libraries':'同步游戏库', 'pricing':'补全商店估值', 'achievements':'同步好友成就'}[stage]
            message = f'{label}遇到数据处理错误（{diagnostic}），已保留已完成的数据。'
            with self.lock:
                if cancel is self.cancel and not cancel.is_set():
                    self.job.update(phase='error', message=message, error=message, error_code=diagnostic)
        finally:
            with self.lock:
                if cancel is self.cancel:
                    self.job['running'] = False
                    if cancel.is_set():
                        self.job['message'] = '已取消好友同步，保留已完成的快照。'

    def sync_achievements(self, ids, key, cancel):
        for index, sid in enumerate(ids):
            if cancel.is_set():
                return
            with self.lock:
                person = next(p for p in self.data['people'] if p['steamid'] == sid)
                self.job.update(phase='achievements', done=index, total=len(ids),
                                message=f'正在统计全成就 {index+1} / {len(ids)} 位…')
                # A stale/private library cannot establish the current scan scope.
                readable = person.get('status') == 'ok'
                games = {g['appid']: g for g in person.get('games', [])}
                if person.get('recent_status') == 'ok':
                    games.update({g['appid']: g for g in person.get('recent_games', [])})
            try:
                if not readable:
                    raise FriendError('游戏库不可读取，无法确认全成就统计范围；请检查 Steam 游戏详情隐私。', 'achievement_private')
                def progress(done, total):
                    with self.lock:
                        if cancel is self.cancel:
                            self.job['message'] = f'正在统计全成就 {index+1} / {len(ids)} 位 · {done} / {total} 款游戏…'
                update = achievement_progress(key, sid, list(games.values()), cancel, progress)
                if update is None:
                    return
            except FriendError as error:
                update = {'achievement_status': 'stale' if person.get('achievement_fetched_at') else 'unavailable',
                          'achievement_error': str(error), 'achievement_error_code': error.code}
            with self.lock:
                if cancel.is_set() or cancel is not self.cancel:
                    return
                person.update(update)
                self.job['done'] = index + 1
                self.save()
            if update.get('achievement_error_code') in ('rate_limit', 'permission'):
                # Stop this phase on global throttling/auth errors. Invalidate all
                # remaining old snapshots instead of leaving apparently fresh ranks.
                with self.lock:
                    if cancel.is_set() or cancel is not self.cancel:
                        return
                    for remaining in self.data['people']:
                        if remaining['steamid'] in ids[index+1:]:
                            remaining.update(achievement_status='stale' if remaining.get('achievement_fetched_at') else 'unavailable',
                                             achievement_error=update['achievement_error'],
                                             achievement_error_code=update['achievement_error_code'])
                    self.save()
                return
            if index + 1 < len(ids) and cancel.wait(.2):
                return

    def disconnect(self):
        with self.lock:
            self.cancel.set()
            self.key = None
            self.access_token = None
            self.job['message'] = '已清除内存中的 API Key；本地好友快照仍可浏览。'
