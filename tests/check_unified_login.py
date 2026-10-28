import sys,json,time,base64,threading,urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
import family_dashboard_server as f
import friend_library as lib
sid='76561198000000000';other='76561198000000001'
def jwt(sid):return 'eyJhbGciOiJSUzI1NiJ9.'+base64.urlsafe_b64encode(json.dumps({'sub':sid,'exp':time.time()+600}).encode()).decode().rstrip('=')+'.synthetic'
token=jwt(sid)
def wait(predicate):
    for _ in range(500):
        if predicate():return
        time.sleep(.01)
    raise AssertionError('job stuck')
# Both official credential forms are transmitted only to the Steam API.
urls=[]
class Response:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return b'{}'
def open_url(req,**kw):urls.append(req.full_url);return Response()
with patch.object(lib.urllib.request,'urlopen',open_url):
    lib.request_api('ISteamUser/GetFriendList/v1',token,steamid=sid)
    lib.request_api('ISteamUser/GetFriendList/v1','a'*32,steamid=sid)
assert 'access_token=' in urls[0] and '&key=' not in urls[0]
assert 'key=' in urls[1] and 'access_token=' not in urls[1]
seen=[]
def api(method,cred,**params):
    seen.append((method,cred))
    if 'GetFriendList' in method:return {'friendslist':{'friends':[{'steamid':other}]}}
    if 'GetPlayerSummaries' in method:return {'response':{'players':[{'steamid':s,'personaname':'Synthetic'} for s in params['steamids'].split(',')]}}
    return {'response':{'game_count':1,'games':[{'appid':10,'name':'Synthetic Game'}]}}
def family(value,progress):return f.normalize({'steamid':sid,'members':[{'steamid':sid}],'games':[{'appid':10,'name':'Synthetic','exclude_reason_code':0,'owner_steamids':[sid]}]})
app=f.App();price_calls=[]
with patch.object(f,'fetch_family',family),patch.object(lib,'request_api',api),patch.object(app.browser_auth,'request',side_effect=AssertionError('must not reopen login')),patch.object(app,'start_prices',lambda *a,**k:price_calls.append(a) or (True,'ok')):
    assert app.start_token(token)[0]
    wait(lambda:app.phase=='idle' and not app.friend_auth['running'] and app.friends.data['people'])
    assert app.friends.status()['auth_configured'] and not app.friends.status()['key_configured']
    assert price_calls and app.friends.data['account_id']==sid and len(app.friends.data['people'])==2
    assert app.start(False)[0];wait(lambda:app.phase=='idle' and not app.friend_auth['running'] and not app.friends.job['running'])
    assert all(cred==token for _,cred in seen)
    assert token not in json.dumps(app.status())
    assert app.friends.start({'action':'sync','ids':[sid,other]})[0]
    wait(lambda:not app.friends.job['running']);assert all(p['status']=='ok' for p in app.friends.data['people'])
# QR requests token once, key once; refresh never creates a new browser login.
app.disconnect();calls=[]
def browser(kind,*args):calls.append(kind);return token if kind=='token' else 'a'*32
with patch.object(f,'fetch_family',family),patch.object(lib,'request_api',api),patch.object(app.browser_auth,'request',browser),patch.object(app,'start_prices',return_value=(True,'ok')):
    assert app.start(True)[0];wait(lambda:app.phase=='idle' and app.friend_auth['phase']=='done' and not app.friend_auth['running'] and not app.friends.job['running'])
    assert calls==['token','key'],calls
    assert app.start(False)[0];wait(lambda:app.phase=='idle' and not app.friend_auth['running'] and not app.friends.job['running'])
    assert calls==['token','key'],calls
# No-family is independent of the friends scope; permission errors do not cause login loops.
app.disconnect()
with patch.object(f,'fetch_family',side_effect=f.SteamFailure('当前账号尚未加入家庭。')),patch.object(lib,'request_api',api),patch.object(app.browser_auth,'request',side_effect=AssertionError('must not open browser')):
    assert app.start_token(token)[0];wait(lambda:app.phase=='error' and app.friend_auth['phase']=='done' and not app.friend_auth['running'] and not app.friends.job['running'])
    assert app.token and app.friends.status()['auth_configured']
app.friends.use_token(jwt(other),other)
assert app.friends.key is None and not app.friends.data['people'] and app.friends.data['account_id']==other
app.disconnect();assert not app.friends.status()['auth_configured']
app.browser_auth.close()
print('PASS shared token/QR, one token + one existing-key lookup, refresh without browser, automatic family prices and friends list, no-family isolation, account switch, redaction and disconnect')
