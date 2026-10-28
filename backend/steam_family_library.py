#!/usr/bin/env python3
"""
Export your full Steam library -- your own games plus every game shared with
you through Steam Families -- to JSON and CSV.

Steam's well-known IPlayerService/GetOwnedGames endpoint only ever returns
games you personally own; family-shared titles are invisible to it. This
script uses IFamilyGroupsService/GetSharedLibraryApps instead, which returns
the merged family library and records which member owns each title.

Requires Python 3.8+ with a supplied token. Interactive browser login also
requires Playwright and its Chromium browser (use a supported Python version).

With no token, open Steam's official login page and obtain the token after
you sign in. Passwords and Steam Guard verification are entered in Steam's
page. Browser sessions and tokens are not saved by this script.

    python3 -m pip install playwright
    python3 -m playwright install chromium
    python3 steam_family_library.py --login

An existing token can still be supplied without Playwright:

    export STEAM_WEBAPI_TOKEN='eyJ0eXAi...'
    python3 steam_family_library.py

MIT licensed.
"""

import argparse
import base64
import csv
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.steampowered.com"
LOGIN_URL = "https://store.steampowered.com/login/"
TOKEN_URL = "https://store.steampowered.com/pointssummary/ajaxgetasyncconfig"

# Steam's sharing eligibility reason; 0 does not guarantee an available copy.
EXCLUDE_REASONS = {
    0: "",
    1: "publisher excluded", 2: "license excluded", 3: "free game",
    4: "private license", 6: "wrong app type", 7: "nonrefundable DLC",
    8: "unreleased", 9: "parent app excluded", 10: "publisher package excluded",
    11: "special package", 12: "developer package", 13: "free weekend",
    15: "invalid package", 16: "recurring license", 17: "wrong license type",
    18: "master subscription", 19: "no shareable apps", 20: "master subscription payment",
    21: "family group payment", 22: "authorized device payment", 23: "auto grant payment",
    24: "pending", 25: "pending refund", 26: "borrowed license", 27: "auto grant",
    28: "timed trial", 29: "free subscription", 30: "inactive",
}


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def browser_token_status(page, timeout_ms):
    """Use Chromium's own cookies, proxy and TLS stack, not Node's HTTP client."""
    target = urllib.parse.urlsplit(TOKEN_URL)
    current = urllib.parse.urlsplit(page.url)
    if (current.scheme, current.netloc) != (target.scheme, target.netloc):
        return None, "等待 Steam 商店登录完成"
    result = page.evaluate("""async ({url, timeout}) => {
        // If the user has opened the token endpoint, consume its existing
        // response without displaying or persisting the token elsewhere.
        if (location.origin + location.pathname === url) {
            try {
                return {status: 200, payload: JSON.parse(document.body.innerText)};
            } catch (_) {}
        }
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeout);
        try {
            const response = await fetch(url, {
                credentials: 'include', cache: 'no-store', redirect: 'error',
                signal: controller.signal,
            });
            if (!response.ok) return {status: response.status};
            try {
                return {status: response.status, payload: await response.json()};
            } catch (_) {
                return {status: response.status, problem: 'invalid_json'};
            }
        } catch (error) {
            return {problem: error.name === 'AbortError' ? 'timeout' : 'network'};
        } finally {
            clearTimeout(timer);
        }
    }""", {"url": TOKEN_URL, "timeout": timeout_ms})
    payload = result.get("payload")
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        token = data.get("webapi_token")
        if isinstance(token, str) and token.strip():
            return token.strip(), "已获取令牌"
    if result.get("problem") == "timeout":
        return None, "Steam 令牌接口请求超时，正在重试"
    if result.get("problem") == "network":
        return None, "浏览器暂时无法连接 Steam 令牌接口，正在重试"
    if result.get("problem") == "invalid_json":
        return None, "Steam 接口返回了非 JSON 内容，正在重试"
    if result.get("status") != 200:
        return None, "Steam 令牌接口 HTTP {}，正在重试".format(result.get("status"))
    return None, "等待 Steam 返回登录令牌；请确认已完成登录验证"


def login_in_browser(timeout=600, channel="chromium"):
    """Get a web token from a fresh browser session after a human signs in.

    Steam OpenID identifies an account but does not grant the Family API
    access needed here. Token requests run inside this temporary browser
    session; no existing browser or Steam client profile is read.
    """
    try:
        from playwright.sync_api import Error as BrowserError
        from playwright.sync_api import sync_playwright
    except ImportError:
        die("browser login requires Playwright. Install it with:\n"
            "  python3 -m pip install playwright\n"
            "  python3 -m playwright install chromium")

    print("Opening Steam login: https://store.steampowered.com/", flush=True)
    print("请在新打开的 Steam 官方页面登录，并完成 Steam Guard 验证。", flush=True)
    print("登录完成后会自动导出；关闭浏览器窗口可取消。", flush=True)
    try:
        with sync_playwright() as pw:
            options = {"headless": False}
            if channel != "chromium":
                options["channel"] = channel
            browser = pw.chromium.launch(**options)
            try:
                context = browser.new_context()
                page = context.new_page()
                deadline = time.monotonic() + timeout
                try:
                    page.goto(LOGIN_URL, wait_until="domcontentloaded",
                              timeout=min(30000, timeout * 1000))
                except BrowserError:
                    if page.is_closed() or not browser.is_connected():
                        die("Steam login was cancelled; no files exported")
                    # Navigation can time out while the login page is still
                    # usable. Continue to check this browser's session.

                next_status_at = 0
                last_status = "尚未获取令牌"
                while time.monotonic() < deadline:
                    if page.is_closed() or not browser.is_connected():
                        die("Steam login was cancelled; no files exported")
                    token = None
                    try:
                        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                        token, last_status = browser_token_status(
                            page, min(10000, remaining_ms),
                        )
                    except BrowserError:
                        last_status = "页面正在跳转或浏览器请求失败，正在重试"
                    if token:
                        steamid_from_token(token)
                        print("Steam 登录成功，正在读取游戏库。", flush=True)
                        return token
                    if time.monotonic() >= next_status_at:
                        print(last_status, flush=True)
                        next_status_at = time.monotonic() + 20
                    remaining_ms = int((deadline - time.monotonic()) * 1000)
                    if remaining_ms > 0:
                        page.wait_for_timeout(min(2000, remaining_ms))
                die("Steam login timed out: {}. Run again or use --login-timeout 1200".format(last_status))
            finally:
                browser.close()
    except BrowserError:
        die("could not open or use the login browser. For Chromium, run:\n"
            "  python3 -m playwright install chromium\n"
            "Or select an installed browser with --browser chrome or --browser msedge.\n"
            "If you closed the login window, run again to retry.")
    except KeyboardInterrupt:
        die("Steam login cancelled; no files exported")


def api_tls_context():
    """Use macOS's CA bundle when python.org Python has no CA file configured."""
    context = ssl.create_default_context()
    if (sys.platform == "darwin"
            and not os.environ.get("SSL_CERT_FILE")
            and not os.environ.get("SSL_CERT_DIR")
            and ssl.get_default_verify_paths().cafile is None
            and os.path.isfile("/etc/ssl/cert.pem")):
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


def api_get(iface_method, params):
    """Call a Steam web API GET endpoint and return its `response` object."""
    url = f"{API}/{iface_method}/?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url, headers={"User-Agent": "steam-family-library/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=api_tls_context()) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        hint = ""
        if e.code in (401, 403):
            hint = "\n\nA 401/403 nearly always means the token expired. Grab a fresh one."
        die(f"{iface_method} returned HTTP {e.code}\n{body}{hint}")
    except urllib.error.URLError as e:
        die(f"could not reach the Steam API: {e.reason}")
    return payload.get("response", {})


def steamid_from_token(token):
    """The webapi_token is a JWT whose `sub` claim is the account's SteamID64."""
    parts = token.split(".")
    if len(parts) != 3:
        die("token does not look like a JWT -- copy only the webapi_token "
            "value, without surrounding quotes or whitespace")
    try:
        claims_b64 = parts[1] + "=" * (-len(parts[1]) % 4)  # restore padding
        claims = json.loads(base64.urlsafe_b64decode(claims_b64))
    except Exception:
        die("could not decode the token payload")

    exp = claims.get("exp")
    if exp and datetime.now(tz=timezone.utc).timestamp() > exp:
        die("that token has expired -- reload the token URL for a new one")

    steamid = claims.get("sub")
    if not steamid:
        die("token has no `sub` claim, so no SteamID could be read from it")
    return steamid


def day(epoch):
    """Steam's rt_* fields are unix seconds. 0 means never."""
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d")


def fetch(token, include_free, include_non_games):
    steamid = steamid_from_token(token)

    fam = api_get(
        "IFamilyGroupsService/GetFamilyGroupForUser/v1",
        {
            "access_token": token,
            "steamid": steamid,
            "include_family_group_response": "true",
        },
    )
    group_id = fam.get("family_groupid")
    if not group_id or fam.get("is_not_member_of_any_group"):
        die("this account is not in a Steam Family group, so there is no "
            "shared library to read")

    group = fam.get("family_group", {})
    members = [m.get("steamid") for m in group.get("members", [])]
    print(f"SteamID64:    {steamid}")
    print(f"Family group: {group.get('name', '?')} ({group_id}), "
          f"{len(members)} members")

    # include_own folds your own games into the same list, so one call returns
    # the whole picture. include_excluded keeps titles the owner has withheld
    # from sharing, so you can see and filter them rather than silently
    # missing them.
    lib = api_get(
        "IFamilyGroupsService/GetSharedLibraryApps/v1",
        {
            "access_token": token,
            "family_groupid": group_id,
            "include_own": "true",
            "include_excluded": "true",
            "include_free": str(bool(include_free)).lower(),
            "include_non_games": str(bool(include_non_games)).lower(),
            "language": "english",
        },
    )
    apps = lib.get("apps", [])
    if not apps:
        die("the API returned zero apps -- the token may belong to a "
            "different account than expected")

    me = str(steamid)
    games = []
    for a in apps:
        owners = [str(o) for o in a.get("owner_steamids", [])]
        reason = a.get("exclude_reason", 0)
        games.append({
            "appid": a.get("appid"),
            "name": a.get("name", ""),
            "source": "own" if me in owners else "family",
            "owned_by_me": me in owners,
            "owner_steamids": owners,
            "playable": reason == 0,
            "exclude_reason_code": reason,
            "exclude_reason": EXCLUDE_REASONS.get(reason, f"unknown ({reason})"),
            "playtime_minutes": a.get("rt_playtime", 0),
            "acquired": day(a.get("rt_time_acquired")),
            "last_played": day(a.get("rt_last_played")),
            "store_url": f"https://store.steampowered.com/app/{a.get('appid')}",
        })

    games.sort(key=lambda g: g["name"].lower())
    return {
        "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        "steamid": steamid,
        "family_groupid": group_id,
        "family_name": group.get("name", ""),
        "members": members,
        "count": len(games),
        "games": games,
    }


CSV_COLUMNS = [
    "appid", "name", "source", "playable", "exclude_reason",
    "playtime_minutes", "acquired", "last_played", "store_url",
]


def write_csv(path, games):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(games)


def main():
    p = argparse.ArgumentParser(
        description="Export your Steam library including Family Sharing games."
    )
    p.add_argument("--json", default="library.json", help="JSON output path")
    p.add_argument("--csv", default="library.csv", help="CSV output path")
    p.add_argument("--include-free", action="store_true",
                   help="include free-to-play titles")
    p.add_argument("--include-non-games", action="store_true",
                   help="include tools, soundtracks, demos and similar")
    p.add_argument("--token", default=os.environ.get("STEAM_WEBAPI_TOKEN", ""),
                   help="webapi_token (defaults to $STEAM_WEBAPI_TOKEN)")
    p.add_argument("--login", action="store_true",
                   help="open Steam login even if a token was supplied")
    p.add_argument("--login-timeout", type=int, default=600, metavar="SECONDS",
                   help="time allowed for browser login (default: 600)")
    p.add_argument("--browser", choices=("chromium", "chrome", "msedge"),
                   default="chromium", help="browser for interactive login")
    args = p.parse_args()
    if args.login_timeout <= 0:
        p.error("--login-timeout must be greater than zero")

    token = args.token.strip().strip("\"'")
    if args.login or not token:
        token = login_in_browser(args.login_timeout, args.browser)

    data = fetch(token, args.include_free, args.include_non_games)
    games = data["games"]

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    write_csv(args.csv, games)

    mine = sum(1 for g in games if g["owned_by_me"])
    blocked = sum(1 for g in games if not g["playable"])
    print(f"\n{len(games)} games")
    print(f"  {mine} owned by you")
    print(f"  {len(games) - mine} from family sharing")
    print(f"  {blocked} not currently playable")
    print(f"\nwrote {args.json} and {args.csv}")


if __name__ == "__main__":
    main()
