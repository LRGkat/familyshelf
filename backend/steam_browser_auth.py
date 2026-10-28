"""One ephemeral Steam browser, owned by a single worker thread. No credential files."""
import queue
import re
import threading
import time
import urllib.parse
import os

import steam_family_library as steam


KEY_URL = 'https://steamcommunity.com/dev/apikey?l=english'
BROWSER_SLOTS = threading.BoundedSemaphore(int(os.getenv('MAX_LOGIN_BROWSERS', '1')))


class AuthError(Exception):
    pass


def read_existing_key(page):
    """Read only the displayed key on the official account page, never page scripts."""
    url = urllib.parse.urlsplit(page.url)
    if url.scheme != 'https' or url.hostname != 'steamcommunity.com' or url.path.rstrip('/') != '/dev/apikey':
        return None
    region = page.locator('#bodyContents')
    if not region.count():
        return None
    match = re.search(r'(?:\bKey|密钥|金鑰|密鑰)\s*[:：]\s*([a-fA-F0-9]{32})\b', region.inner_text(timeout=2000), re.I)
    return match.group(1) if match else None


def key_page_status(page):
    """Report actionable states without returning page text or credentials."""
    url = urllib.parse.urlsplit(page.url)
    if url.scheme != 'https' or url.hostname != 'steamcommunity.com':
        raise AuthError('Steam 官方 Key 页面未能打开。请检查网络，或点击“重新打开 / 重试”。')
    if url.path.startswith('/login'):
        return '需要在 Steam 官方窗口重新登录；登录后会继续读取 Key。'
    if url.path.rstrip('/') != '/dev/apikey':
        return None
    region = page.locator('#bodyContents')
    if not region.count():
        return None
    text = region.inner_text(timeout=2000).lower()
    if any(word in text for word in ('access denied', 'limited account', '拒绝访问', '拒絕存取', '受限帐户', '受限账户')):
        raise AuthError('Steam 拒绝此账号访问 API Key 页面。请查看官方页面的账号限制说明；此时无法自动获取 Key。')
    if any(word in text for word in ('steam guard', 'confirm your', 'confirmation', '验证器', '确认请求', '確認請求')):
        return '请在 Steam 手机令牌中确认官方申请；完成后会自动读取 Key，无需复制。'
    if any(word in text for word in ('register', 'domain name', '注册', '註冊', '域名')):
        return '此账号尚未显示已有 Key。请在 Steam 官方页面完成申请及必要确认，完成后自动读取，无需复制。'
    return None


class SteamBrowserAuth:
    def __init__(self):
        self.commands = queue.Queue()
        self.closed = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.qr = None

    def qr_image(self):
        with self.lock:
            return self.qr

    def capture_qr(self, page):
        # Steam currently renders a data-URI GIF; older builds use a canvas.
        # Only capture that isolated square image on the official login page.
        current = urllib.parse.urlsplit(page.url)
        if current.scheme != 'https' or current.hostname not in ('store.steampowered.com', 'steamcommunity.com') or not current.path.startswith('/login'):
            return
        for element in page.locator('canvas, img[src^="data:image/"]').all():
            box = element.bounding_box()
            if box and 120 <= box['width'] <= 420 and 120 <= box['height'] <= 420 and abs(box['width'] - box['height']) < 2:
                image = element.screenshot(timeout=2000)
                with self.lock:
                    self.qr = image
                return

    def request(self, kind, cancel, progress):
        if self.closed.is_set():
            raise AuthError('登录服务已关闭。')
        task = {'kind': kind, 'cancel': cancel, 'progress': progress, 'done': threading.Event()}
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
            self.commands.put(task)
        while not task['done'].wait(.2):
            if cancel.is_set() or self.closed.is_set():
                raise AuthError('已取消 Steam 连接。')
        if 'error' in task:
            raise AuthError(task['error'])
        return task.get('result')

    def reset(self):
        with self.lock:
            self.qr = None
        self.commands.put({'kind': 'reset'})

    def close(self):
        self.closed.set()
        self.commands.put({'kind': 'reset'})

    def _run(self):
        browser = context = page = None
        slot = False
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                try:
                    while not self.closed.is_set():
                        try:
                            task = self.commands.get(timeout=.5)
                        except queue.Empty:
                            continue
                        if task['kind'] == 'reset':
                            if browser:
                                browser.close()
                            browser = context = page = None
                            if slot:
                                BROWSER_SLOTS.release()
                                slot = False
                            continue
                        try:
                            if task['cancel'].is_set():
                                raise AuthError('已取消 Steam 连接。')
                            if not browser or not browser.is_connected():
                                if not slot:
                                    if not BROWSER_SLOTS.acquire(blocking=False):
                                        raise AuthError('当前有其他访客正在扫码，请约一分钟后重试；已有 Key 也可手动连接好友。')
                                    slot = True
                                browser = pw.chromium.launch(headless=True, channel='chromium', args=['--disable-dev-shm-usage'])
                                context = browser.new_context(accept_downloads=False, viewport={'width':1000,'height':850})
                                page = None
                            if page is None or page.is_closed():
                                page = context.new_page()
                            page.bring_to_front()
                            target = KEY_URL if task['kind'] == 'key' else steam.LOGIN_URL
                            task['progress']('正在打开 Steam 官方 API Key 页面…' if task['kind'] == 'key' else '请在 Steam 官方窗口扫码或登录…')
                            try:
                                response = page.goto(target, wait_until='domcontentloaded', timeout=20000)
                                if task['kind'] == 'key' and response and response.status >= 400:
                                    raise AuthError(f'Steam 官方 Key 页面暂不可用（HTTP {response.status}），请稍后重试。')
                            except AuthError:
                                raise
                            except Exception:
                                # A timeout may occur after the account page has rendered.
                                if task['kind'] == 'key' and not read_existing_key(page):
                                    raise AuthError('Steam 官方 Key 页面加载失败。请检查网络后点击“重新打开 / 重试”；也可直接使用已有 Key 连接。') from None
                            deadline = time.monotonic() + 180
                            unknown_since = time.monotonic()
                            qr_started = time.monotonic()
                            while time.monotonic() < deadline:
                                if self.closed.is_set() or task['cancel'].is_set():
                                    raise AuthError('已取消 Steam 连接。')
                                if page.is_closed() or not browser.is_connected():
                                    raise AuthError('Steam 窗口已关闭，可再次点击自动连接。')
                                if task['kind'] == 'key':
                                    try:
                                        key = read_existing_key(page)
                                    except Exception:
                                        # Registration and Steam Guard can navigate while we poll.
                                        key = None
                                    if key:
                                        task['result'] = key
                                        break
                                    message = key_page_status(page)
                                    if message and ('尚未显示已有 Key' in message or '需要在 Steam 官方窗口重新登录' in message):
                                        raise AuthError('账号尚无可读取的 Key。请在 Steam 官方 API Key 页面完成申请，再展开“手动连接”输入 SteamID 与 Key。')
                                    if message:
                                        unknown_since = time.monotonic()
                                        task['progress'](message)
                                    elif time.monotonic() - unknown_since > 30:
                                        raise AuthError('官方页面未显示可读取的 Key 或申请状态，已停止等待。请点击“重新打开 / 重试”，或直接使用已有 Key 连接。')
                                    else:
                                        task['progress']('正在加载官方 API Key 页面；已有 Key 会自动读取。也可直接使用手动连接。')
                                else:
                                    try:
                                        token, message = steam.browser_token_status(page, 8000)
                                    except Exception:
                                        token, message = None, '等待 Steam 登录完成…'
                                    if token:
                                        task['result'] = token
                                        break
                                    try:
                                        self.capture_qr(page)
                                    except Exception:
                                        pass
                                    if not self.qr_image() and time.monotonic() - qr_started > 60:
                                        raise AuthError('Steam 未能加载登录二维码，可能限制了云端网络。请稍后重试；好友榜也可使用已有 API Key 手动连接。')
                                    task['progress']('请用 Steam 手机应用扫描下方二维码，并确认此次云端登录。' if self.qr_image() else '正在加载 Steam 官方二维码…')
                                page.wait_for_timeout(1200)
                            else:
                                raise AuthError('Steam 授权等待超时，请再次连接。')
                        except AuthError as error:
                            task['error'] = str(error)
                        except Exception:
                            # Browser errors may contain page text or credential-bearing URLs.
                            task['error'] = '无法完成 Steam 自动连接，请重试或使用手动连接。'
                        finally:
                            with self.lock:
                                self.qr = None
                            if task['kind'] == 'key' or 'error' in task:
                                if browser:
                                    browser.close()
                                browser = context = page = None
                                if slot:
                                    BROWSER_SLOTS.release()
                                    slot = False
                            task['done'].set()
                finally:
                    if browser:
                        browser.close()
                    if slot:
                        BROWSER_SLOTS.release()
                        slot = False
        except Exception:
            self.closed.set()
            while not self.commands.empty():
                task = self.commands.get()
                if 'done' in task:
                    task['error'] = '无法打开登录浏览器，请通过看板启动器安装 Chromium 后重试。'
                    task['done'].set()
