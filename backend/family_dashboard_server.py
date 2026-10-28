#!/usr/bin/env python3
"""Local Steam Family dashboard. Credentials live only in process memory."""
import argparse
import base64
import concurrent.futures
import copy
import csv
import io
import json
import math
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import steam_family_library as steam
from friend_library import Friends, FriendError
from steam_browser_auth import SteamBrowserAuth, AuthError

ROOT = Path(__file__).resolve().parent
REASONS = {0:'可共享',1:'发行商不支持共享',2:'许可证不支持共享',3:'免费游戏',4:'私密许可证',6:'非游戏类型',7:'不可退款 DLC',8:'尚未发行',9:'主游戏不可共享',10:'发行商限制的礼包',11:'特殊礼包',12:'开发者礼包',13:'免费周末',15:'无效礼包',16:'订阅许可证',17:'许可证类型不支持',18:'主订阅限制',19:'无可共享应用',20:'订阅支付限制',21:'家庭支付限制',22:'设备支付限制',23:'自动授予支付限制',24:'待处理许可证',25:'退款处理中',26:'借用许可证',27:'自动授予许可证',28:'限时试玩',29:'免费订阅',30:'未激活'}
LEGACY = {'':0,'excluded by owner':1,'not eligible for sharing':2,'region or DRM restricted':3}
REGIONS = {'cn':'CNY','us':'USD','hk':'HKD'}


def now():
    return datetime.now(timezone.utc).isoformat()


def normalize(data):
    """Migrate the original export without misreading its old reason labels."""
    data = copy.deepcopy(data)
    data['members'] = [({'steamid':str(m)} if isinstance(m,(str,int)) else m) for m in data.get('members',[])]
    members = {str(m['steamid']) for m in data['members']}
    seen = set()
    games = []
    for game in data.get('games',[]):
        try:
            appid = int(game['appid'])
        except (ValueError,KeyError,TypeError):
            continue
        if appid <= 0 or appid in seen:
            continue
        seen.add(appid)
        game['appid'] = appid
        reason = game.get('exclude_reason_code')
        if reason is None:
            reason = LEGACY.get(game.get('exclude_reason'))
            if reason is None:
                label = str(game.get('exclude_reason',''))
                try: reason = int(label.removeprefix('unknown (').removesuffix(')'))
                except ValueError: reason = 0 if game.get('playable') else -1
        game['exclude_reason_code'] = reason
        game['exclude_reason'] = REASONS.get(reason,'其他共享限制')
        game['shareable'] = reason == 0
        game['owner_steamids'] = list(dict.fromkeys(str(x) for x in game.get('owner_steamids',[]) if str(x).isdigit()))
        for sid in game['owner_steamids']:
            if sid not in members:
                data['members'].append({'steamid':sid})
                members.add(sid)
        games.append(game)
    data['games'] = games
    data['count'] = len(games)
    return data


def read_json(path, default):
    # Public deployment always starts with an empty, isolated session.
    return copy.deepcopy(default)


def save_json(path, value):
    # No account snapshots or price caches are written by the cloud service.
    pass


def public_profile(sid):
    try:
        req = urllib.request.Request(f'https://steamcommunity.com/profiles/{sid}/?xml=1',headers={'User-Agent':'FamilyShelf/1.0'})
        with urllib.request.urlopen(req,timeout=12,context=steam.api_tls_context()) as r:
            root = ET.fromstring(r.read(2_000_000))
        result = {'steamid':sid}
        if root.findtext('steamID'): result['personaname'] = root.findtext('steamID')
        avatar = root.findtext('avatarFull','')
        parsed = urllib.parse.urlsplit(avatar)
        if parsed.scheme == 'https' and any((parsed.hostname or '').endswith(d) for d in ('.steamstatic.com','.steamcommunity.com','.steamcdn-a.akamaihd.net')):
            result['avatar'] = avatar
        return result
    except Exception:
        return {'steamid':sid}


class SteamFailure(Exception):
    def __init__(self,message,auth=False):
        super().__init__(message)
        self.auth = auth


def parse_login_token(value):
    """Check input shape only; Steam's API verifies actual authorization."""
    if not isinstance(value, str) or not 1 <= len(value) <= 12000:
        raise ValueError('请输入有效的 Steam 登录令牌。')
    token = value.strip()
    if token.startswith('{'):
        try:
            token = json.loads(token).get('webapi_token')
        except (ValueError, AttributeError):
            token = None
    if isinstance(token, str) and token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
        raise ValueError('令牌格式不正确，请复制完整的 webapi_token，而非 API Key。')
    try:
        payload = token.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if not isinstance(claims, dict) or not re.fullmatch(r'\d{17}', str(claims.get('sub', ''))):
            raise ValueError()
        expiry = claims.get('exp')
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('无法识别令牌，请从 Steam 官方页面重新获取完整的 webapi_token。') from None
    if expiry <= time.time():
        raise ValueError('令牌已过期，请重新获取后登录。')
    return token, str(claims['sub'])


def api_call(method,params):
    url = f'{steam.API}/{method}/?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url,headers={'User-Agent':'FamilyShelf/1.0'})
    try:
        with urllib.request.urlopen(req,timeout=30,context=steam.api_tls_context()) as r:
            return json.load(r).get('response',{})
    except urllib.error.HTTPError as e:
        raise SteamFailure('登录已过期，请重新连接 Steam。' if e.code in (401,403) else f'Steam 接口暂时不可用（HTTP {e.code}），请稍后重试。',e.code in (401,403)) from None
    except Exception:
        # Never include a request URL: its query string contains a credential.
        raise SteamFailure('无法连接 Steam，请检查网络后重试。') from None


def fetch_family(token,progress):
    try: sid = str(steam.steamid_from_token(token))
    except SystemExit: raise SteamFailure('Steam 登录已过期，请重新连接。',True) from None
    progress('正在读取家庭成员…')
    fam = api_call('IFamilyGroupsService/GetFamilyGroupForUser/v1',{'access_token':token,'steamid':sid,'include_family_group_response':'true'})
    gid = fam.get('family_groupid')
    if not gid or fam.get('is_not_member_of_any_group'):
        raise SteamFailure('这个 Steam 账号尚未加入家庭，请切换账号或先创建家庭。')
    group = fam.get('family_group')
    if not group:
        group = api_call('IFamilyGroupsService/GetFamilyGroup/v1',{'access_token':token,'family_groupid':gid})
    progress('正在同步共享游戏与持有人…')
    library = api_call('IFamilyGroupsService/GetSharedLibraryApps/v1',{'access_token':token,'family_groupid':gid,'include_own':'true','include_excluded':'true','include_non_games':'false','language':'schinese','max_apps':10000})
    apps = library.get('apps',[])
    if not apps:
        raise SteamFailure('Steam 暂未返回家庭游戏数据，保留上次快照，请稍后刷新。')
    games = []
    for a in apps:
        owners = [str(o) for o in a.get('owner_steamids',[])]
        reason = int(a.get('exclude_reason',0))
        games.append({'appid':a.get('appid'),'name':a.get('name','未命名游戏'),'owner_steamids':owners,'owned_by_me':sid in owners,'exclude_reason_code':reason,'last_played':steam.day(a.get('rt_last_played')),'acquired':steam.day(a.get('rt_time_acquired'))})
    members = group.get('members',[])
    progress('正在补全成员昵称和头像…')
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        profiles = list(pool.map(public_profile,[str(m['steamid']) for m in members]))
    for member,profile in zip(members,profiles): member.update(profile)
    return normalize({'fetched_at':now(),'steamid':sid,'family_groupid':str(gid),'family_name':group.get('name','Steam 家庭'),'members':members,'games':games})


class App:
    def __init__(self):
        seed = read_json(ROOT/'dashboard_data.json',None) or read_json(ROOT/'library.json',{'members':[],'games':[]})
        self.data = normalize(seed)
        self.lock = threading.RLock()
        self.token = None
        self.login_method = 'token'
        self.browser_key_pending = False
        self.csrf = secrets.token_urlsafe(32)
        self.phase = 'idle'
        self.message = '连接 Steam，开始同步你的家庭收藏。'
        self.cancel = threading.Event()
        self.job_id = 0
        self.revision = 1
        self.prices = read_json(ROOT/'price_cache.json',{})
        self.price_job = {'running':False,'done':0,'total':0,'message':'尚未加载商店价格','region':'cn'}
        self.price_cancel = threading.Event()
        self.friends = Friends(ROOT, self.price_friend_libraries)
        self.browser_auth = SteamBrowserAuth()
        self.friend_auth_cancel = threading.Event()
        self.friend_auth = {'running':False, 'phase':'idle', 'message':'登录 Steam 后自动连接好友，无需复制 API Key。'}

    def status(self):
        with self.lock:
            return {'data':self.data,'session':{'connected':bool(self.token),'phase':self.phase,'message':self.message},'revision':self.revision,'csrf':self.csrf,'prices':self.prices,'price_job':dict(self.price_job),'friends':{**self.friends.status(),'auth':dict(self.friend_auth)}}

    def progress(self,message):
        with self.lock: self.message = message

    def start(self,login=False):
        with self.lock:
            if self.phase in ('login','refresh'): return False,'已有登录或同步任务正在进行。'
            if login and self.friends.status()['job']['running']: return False,'好友正在同步，请先等待完成或取消。'
            if login and self.friend_auth['running']: return False,'好友正在等待 Steam 授权，请先完成或取消。'
            if not login and not self.token: return False,'请先连接 Steam。'
            self.cancel = threading.Event()
            if login:
                self.login_method = 'qr'
                self.browser_key_pending = True
            self.phase = 'login' if login else 'refresh'
            self.message = '正在打开 Steam 官方登录窗口…' if login else '正在同步家庭数据…'
            self.job_id += 1
            threading.Thread(target=self.run,args=(login,self.job_id,self.cancel),daemon=True).start()
            return True,self.message

    def login(self,cancel,progress=None):
        try:
            return self.browser_auth.request('token', cancel, progress or self.progress)
        except AuthError as error:
            raise SteamFailure(str(error)) from None

    def start_token(self, value):
        try:
            token, account = parse_login_token(value)
        except ValueError as error:
            return False, str(error)
        with self.lock:
            if self.phase == 'refresh' or self.friends.status()['job']['running']:
                return False, '数据正在同步，请先等待完成或取消同步。'
            self.cancel.set()
            self.cancel_friend_auth()
            self.browser_auth.reset()
            # Do not reuse another account's friend credentials.
            friend_account = self.friends.status()['data'].get('account_id')
            if friend_account and friend_account != account:
                self.friends.disconnect()
            self.login_method = 'token'
            self.browser_key_pending = False
            self.friend_auth['message'] = '将复用本次令牌连接好友，不再发起登录。'
            self.job_id += 1
            self.cancel = threading.Event()
            self.phase = 'refresh'
            self.message = '正在验证令牌并同步家庭库…'
            threading.Thread(target=self.run, args=(False, self.job_id, self.cancel, token), daemon=True).start()
            return True, self.message

    def start_friend_auth(self):
        with self.lock:
            if not self.token:
                return False, '请先使用统一入口登录 Steam，随后会自动连接所有榜单。'
            if self.friend_auth['running']:
                return False, '正在等待 Steam 官方窗口，请完成登录或确认。'
            if self.phase in ('login', 'refresh'):
                return False, '家庭库正在连接，完成后会自动连接好友。'
            if self.friends.status()['job']['running']:
                return False, '好友同步正在进行，请稍后再连接。'
            self.friend_auth_cancel = threading.Event()
            self.friend_auth = {'running':True, 'phase':'login', 'message':'正在复用本次 Steam 登录…'}
            threading.Thread(target=self.run_friend_auth, args=(self.friend_auth_cancel,), daemon=True).start()
        return True, '正在自动连接好友，请在 Steam 官方窗口完成必要的确认。'

    def run_friend_auth(self,cancel):
        def progress(message):
            with self.lock:
                if cancel is self.friend_auth_cancel and not cancel.is_set():
                    self.friend_auth['message'] = message
        try:
            # Reuse the shared login; never request a second Steam login for friends.
            with self.lock:
                token = self.token
                read_browser_key = self.browser_key_pending
                self.browser_key_pending = False
            account = str(steam.steamid_from_token(token))
            with self.lock:
                if cancel.is_set() or cancel is not self.friend_auth_cancel:
                    return
                self.friends.use_token(token, account)
                self.friend_auth['phase'] = 'key'
            key = self.friends.key
            if read_browser_key and key:
                self.browser_auth.reset()
            if read_browser_key and not key:
                try:
                    key = self.browser_auth.request('key', cancel, progress)
                except AuthError:
                    # Some accounts have no key. Try the existing token without another login.
                    progress('未读取到 API Key，改用本次令牌读取好友；权限不足时可补充 Key。')
            with self.lock:
                if cancel.is_set() or cancel is not self.friend_auth_cancel:
                    return
                ok, message = self.friends.start({'action':'list','steamid':account,'key':key})
                self.friend_auth.update(phase='done' if ok else 'error', message='已复用登录，自动加载全部好友、同步游戏库并排名。' if ok else message)
        except AuthError as error:
            progress(str(error))
            with self.lock:
                if cancel is self.friend_auth_cancel:
                    self.friend_auth['phase'] = 'error'
        except SystemExit:
            progress('无法识别当前 Steam 登录，请重新连接。')
            with self.lock:
                if cancel is self.friend_auth_cancel:
                    self.friend_auth['phase'] = 'error'
        except Exception:
            progress('自动连接暂未完成，请重试或使用手动连接。')
            with self.lock:
                if cancel is self.friend_auth_cancel:
                    self.friend_auth['phase'] = 'error'
        finally:
            with self.lock:
                if cancel is self.friend_auth_cancel:
                    self.friend_auth['running'] = False
                    if cancel.is_set():
                        self.friend_auth.update(phase='idle',message='已取消好友自动连接。')

    def cancel_friend_auth(self):
        with self.lock:
            self.friend_auth_cancel.set()
            self.friend_auth.update(running=False, phase='idle', message='已取消好友自动连接。')

    def run(self,login,job,cancel,supplied_token=None):
        def progress(message):
            with self.lock:
                if job == self.job_id and not cancel.is_set():
                    self.message = message
        try:
            token = supplied_token if supplied_token is not None else self.login(cancel,progress) if login else self.token
            if cancel.is_set(): return
            with self.lock:
                if job != self.job_id: return
                account = str(steam.steamid_from_token(token))
                if str(self.data.get('steamid', '')) not in ('', account):
                    self.data = normalize({'steamid': account, 'members': [], 'games': []})
                    self.revision += 1
                self.token = token
                self.phase = 'refresh'
            data = fetch_family(token,progress)
            with self.lock:
                if cancel.is_set() or job != self.job_id: return
                self.data = data
                self.revision += 1
                save_json(ROOT/'dashboard_data.json',data)
                self.phase = 'idle'
                self.message = '已同步最新家庭库。'
        except SteamFailure as e:
            with self.lock:
                if job != self.job_id: return
                if e.auth:
                    self.token = None
                    self.friends.disconnect()
                self.phase = 'error'
                self.message = str(e)
        except Exception:
            with self.lock:
                if job != self.job_id: return
                self.phase = 'error'
                self.message = '同步未完成，已保留上次数据。请重试。'
        finally:
            with self.lock:
                if cancel.is_set() and job == self.job_id:
                    self.phase = 'idle'
                    self.message = '已取消操作，保留上次快照。'
            with self.lock:
                if not cancel.is_set() and self.token and job == self.job_id:
                    if self.data.get('games'):
                        self.start_prices('cn')
                    self.start_friend_auth()

    def disconnect(self):
        with self.lock:
            self.cancel.set()
            self.job_id += 1
            self.token = None
            self.browser_key_pending = False
            self.cancel_friend_auth()
            self.friends.disconnect()
            self.browser_auth.reset()
            self.phase = 'idle'
            self.message = '已断开连接；本次快照仍可浏览，关闭页面后会过期清除。'

    def price_friend_libraries(self, ids, region, cancel):
        # One backend job owns list -> libraries -> prices, even with no page open.
        with self.friends.lock:
            if cancel.is_set() or cancel is not self.friends.cancel:
                return
            self.friends.job.update(phase='pricing', message='游戏库已同步，正在排队补全商店估值…')
            readable = {p['steamid'] for p in self.friends.data['people']
                        if p.get('status') in ('ok', 'stale') and p.get('games')}
        ids = [sid for sid in ids if sid in readable]
        for offset in range(0, len(ids), 100):
            while not cancel.is_set():
                with self.lock:
                    if not self.price_job['running']:
                        ok, message = self.start_prices(region, 'friends', ids[offset:offset+100])
                        if not ok:
                            raise FriendError(message)
                        pricing_cancel = self.price_cancel
                        break
                cancel.wait(.2)
            if cancel.is_set():
                return
            while not cancel.is_set():
                with self.lock:
                    running = self.price_job['running'] and self.price_cancel is pricing_cancel
                    message = self.price_job['message']
                    price_error = self.price_job.get('error', '')
                if not running:
                    break
                with self.friends.lock:
                    if cancel is self.friends.cancel:
                        self.friends.job['message'] = '正在补全好友估值：' + message
                cancel.wait(.2)
            if cancel.is_set():
                pricing_cancel.set()
                return
            if pricing_cancel.is_set() or price_error:
                raise FriendError(price_error or '估值已暂停，可点击补全估值继续。')

    def cancel_friends(self):
        self.friends.cancel.set()
        with self.lock:
            if self.price_job.get('scope') == 'friends':
                self.price_cancel.set()

    def start_prices(self,region,scope='family',ids=None):
        if region not in REGIONS: return False,'不支持的商店地区。'
        with self.lock:
            if self.price_job['running']: return False,'正在补全价格。'
            self.price_cancel = threading.Event()
            if scope == 'friends':
                if not isinstance(ids,list) or not 1 <= len(ids) <= 100 or any(not isinstance(s,str) for s in ids):
                    return False,'请先勾选 1–100 位好友。'
                people = self.friends.status()['data']['people']
                apps = sorted({g['appid'] for p in people if p['steamid'] in ids and p.get('status') in ('ok','stale') for g in p.get('games',[])})
                if not apps: return False,'所选成员尚无可读取的游戏库，请先同步。'
            elif scope == 'family':
                apps = [g['appid'] for g in self.data['games'] if g.get('shareable')]
            else: return False,'未知的价格查询范围。'
            self.price_job = {'running':True,'done':0,'total':len(apps),'message':'正在读取 Steam 商店价格…','region':region,'scope':scope}
            threading.Thread(target=self.fetch_prices,args=(region,apps,self.price_cancel),daemon=True).start()
        return True,'正在补全价格，您可以继续浏览。'

    def fetch_prices(self,region,apps,cancel):
        # Steam supports multiple appids when price_overview is the ONLY filter.
        # Keep batches small, and never infer zero from a missing price.
        interrupted = False
        network_error = False
        try:
            with self.lock:
                pending = []
                for appid in apps:
                    cached = self.prices.get(f'{region}:{appid}')
                    age = time.time()-(cached or {}).get('checked',0)
                    if not cached or age > (86400 if cached.get('known') else 1800): pending.append(appid)
                self.price_job['done'] = len(apps)-len(pending)
            for offset in range(0,len(pending),40):
                if cancel.is_set(): break
                batch = pending[offset:offset+40]
                try:
                    params = urllib.parse.urlencode({'appids':','.join(map(str,batch)),'cc':region,'filters':'price_overview'})
                    req = urllib.request.Request('https://store.steampowered.com/api/appdetails?'+params,headers={'User-Agent':'FamilyShelf/1.0'})
                    with urllib.request.urlopen(req,timeout=25,context=steam.api_tls_context()) as r: payload = json.load(r)
                    if not isinstance(payload,dict): raise ValueError('invalid store response')
                    for appid in batch:
                        item = payload.get(str(appid),{})
                        details = item.get('data') if isinstance(item,dict) and item.get('success') else None
                        price = details.get('price_overview') if isinstance(details,dict) else None
                        known = bool(isinstance(price,dict) and price.get('currency') == REGIONS[region]
                                     and isinstance(price.get('initial'),int) and isinstance(price.get('final'),int))
                        entry = {'checked':time.time(),'known':known,'currency':REGIONS[region]}
                        if known: entry.update({'initial':price['initial'],'final':price['final']})
                        with self.lock: self.prices[f'{region}:{appid}'] = entry
                except urllib.error.HTTPError as e:
                    if e.code in (429,403):
                        interrupted = True
                        with self.lock: self.price_job['message'] = 'Steam 商店限制请求，已保存进度；稍后可继续补全。'
                        break
                    network_error = True
                except Exception:
                    network_error = True
                with self.lock:
                    self.price_job['done'] += len(batch)
                    self.price_job['message'] = f"已处理 {self.price_job['done']} / {len(apps)} 款游戏"
                    save_json(ROOT/'price_cache.json',self.prices)
                if cancel.wait(1.2): break
        finally:
            with self.lock:
                self.price_job['running'] = False
                if cancel.is_set(): self.price_job['message'] = '价格加载已暂停，进度已保存。'
                elif not interrupted:
                    self.price_job['message'] = '部分商店请求失败，已保留价格；可以再次补全。' if network_error else '商店价格查询完成；无报价游戏保持未知。'
                self.price_job['error'] = self.price_job['message'] if interrupted or network_error else ''
                save_json(ROOT/'price_cache.json',self.prices)


class Handler(BaseHTTPRequestHandler):
    server_version = 'FamilyShelf'
    def log_message(self,*args): pass

    def send(self,status,body,ctype='application/json; charset=utf-8'):
        raw = body.encode('utf-8') if isinstance(body,str) else body
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.end_headers()
        self.wfile.write(raw)

    def valid_host(self):
        return self.headers.get('Host') in self.server.allowed_hosts

    def do_GET(self):
        if not self.valid_host(): return self.send(403,'{}')
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/state':
            return self.send(200,json.dumps(self.server.app.status(),ensure_ascii=False))
        if path in ('/','/family_dashboard.html'):
            return self.send(200,(ROOT/'family_dashboard.html').read_bytes(),'text/html; charset=utf-8')
        if path == '/favicon.ico': return self.send(204,b'','image/x-icon')
        self.send(404,'{}')

    def do_POST(self):
        origin = self.headers.get('Origin','')
        if (not self.valid_host() or origin not in self.server.allowed_origins or self.headers.get('X-FamilyShelf-CSRF') != self.server.app.csrf):
            return self.send(403,json.dumps({'error':'请求来源无效，请从本地看板重新操作。'},ensure_ascii=False))
        try:
            size = int(self.headers.get('Content-Length','0'))
            if not 0<=size<=65536: raise ValueError
            body = json.loads(self.rfile.read(size) or '{}')
            if not isinstance(body,dict): raise ValueError
        except (ValueError,TypeError): return self.send(400,'{"error":"请求格式无效"}')
        app = self.server.app
        path = urllib.parse.urlsplit(self.path).path
        if path == '/api/login': ok,msg = app.start(True)
        elif path == '/api/refresh': ok,msg = app.start(False)
        elif path == '/api/disconnect':
            app.disconnect();ok,msg = True,'已断开连接。'
        elif path == '/api/cancel':
            app.cancel.set();ok,msg = True,'正在取消…'
        elif path == '/api/friends/login': ok,msg = app.start_friend_auth()
        elif path == '/api/friends/login/cancel':
            app.cancel_friend_auth();ok,msg = True,'已取消自动连接。'
        elif path == '/api/friends':
            if app.friend_auth['running']:
                ok,msg = False,'请先完成或取消自动连接。'
            else:
                ok,msg = app.friends.start(body)
        elif path == '/api/friends/cancel':
            app.cancel_friends();ok,msg = True,'正在取消好友同步…'
        elif path == '/api/friends/disconnect':
            app.cancel_friend_auth();app.friends.disconnect();ok,msg = True,'已清除内存中的 API Key。'
        elif path == '/api/prices': ok,msg = app.start_prices(body.get('region','cn'),body.get('scope','family'),body.get('ids'))
        elif path == '/api/prices/cancel':
            app.price_cancel.set();ok,msg = True,'正在暂停价格查询…'
        else: return self.send(404,'{}')
        self.send(202 if ok else 409,json.dumps({'message':msg,'ok':ok},ensure_ascii=False))


def make_server(port):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    port = server.server_address[1]
    server.allowed_hosts = {f'127.0.0.1:{port}',f'localhost:{port}'}
    server.allowed_origins = {f'http://{x}' for x in server.allowed_hosts}
    server.app = App()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--no-open',action='store_true')
    args = parser.parse_args()
    try: server = make_server(args.port)
    except OSError:
        print(f'端口 {args.port} 已被占用，请关闭旧看板或通过 --port 指定其他端口。')
        raise SystemExit(1)
    url = f'http://127.0.0.1:{server.server_address[1]}'
    print(f'家庭贡献看板已启动：{url}\n保持此窗口开启；按 Ctrl+C 停止。',flush=True)
    if not args.no_open: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.app.disconnect()
        server.app.friends.disconnect()
        server.app.browser_auth.close()
        server.app.price_cancel.set()
        server.server_close()


if __name__ == '__main__': main()
