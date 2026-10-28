"""Synthetic contracts + complete server-owned pipeline; no real credentials."""
import sys,json,time,base64,threading,urllib.parse,urllib.error
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
import family_dashboard_server as f
import friend_library as lib
sid='76561198000000000';other='76561198000000001'
def jwt(sid):return 'eyJhbGciOiJSUzI1NiJ9.'+base64.urlsafe_b64encode(json.dumps({'sub':sid,'exp':time.time()+600}).encode()).decode().rstrip('=')+'.synthetic'
token=jwt(sid)
def wait(predicate):
    for _ in range(1000):
        if predicate():return
        time.sleep(.01)
    raise AssertionError('job stuck')
class Response:
    def __init__(self,payload=None,headers=None):self.payload=payload or {};self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.payload).encode()
urls=[]
def open_url(req,**kw):urls.append(req.full_url);return Response()
with patch.object(lib.urllib.request,'urlopen',open_url):
    lib.request_api('IFriendsListService/GetFriendsList/v1',token)
    lib.request_api('IPlayerService/GetOwnedGames/v1',token,steamid=sid,include_appinfo=True)
    lib.request_api('ISteamUser/GetFriendList/v1','a'*32,steamid=sid)
q=[urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in urls]
assert json.loads(q[0]['input_json'][0])=={} and q[0]['access_token']==[token]
assert json.loads(q[1]['input_json'][0])['include_appinfo'] is True
assert q[2]['key']==['a'*32] and 'access_token' not in q[2]
with patch.object(lib.urllib.request,'urlopen',return_value=Response(headers={'x-eresult':'15'})):
    try:lib.request_api('IFriendsListService/GetFriendsList/v1',token);raise AssertionError('must reject EResult')
    except lib.FriendError as e:assert e.code=='permission' and token not in str(e)
seen=[]
def api(method,cred,**params):
    seen.append((method,cred))
    if 'GetFriendsList' in method:return {'response':{'friendslist':{'friends':[{'ulfriendid':other,'efriendrelationship':3},{'ulfriendid':'103582791429521412','efriendrelationship':3},{'ulfriendid':'76561198000000002','efriendrelationship':2}]}}}
    if 'GetFriendList' in method:return {'friendslist':{'friends':[{'steamid':other}]}}
    if 'GetPlayerLinkDetails' in method:return {'response':{'accounts':[{'public_data':{'steamid':s,'persona_name':'Synthetic'},'private_data':{'account_name':'must-never-leak'}} for s in params['steamids']]}}
    if 'GetPlayerSummaries' in method:return {'response':{'players':[{'steamid':s,'personaname':'Synthetic'} for s in params['steamids'].split(',')]}}
    if 'GetRecentlyPlayedGames' in method:return {'response':{'total_count':0}}
    assert 'GetOwnedGames' in method
    return {'response':{'game_count':1,'games':[{'appid':10,'name':'Synthetic Game'}]}}
def family(value,progress):return f.normalize({'steamid':sid,'members':[{'steamid':sid}],'games':[{'appid':10,'name':'Synthetic','exclude_reason_code':0,'owner_steamids':[sid]}]})
store=[]
def priced(self,region,apps,cancel):
    store.append((region,list(apps)))
    with self.lock:
        for aid in apps:self.prices[f'{region}:{aid}']={'known':True,'checked':time.time(),'initial':5000,'final':3000,'currency':f.REGIONS[region]}
        self.price_job.update(running=False,done=len(apps),message='complete',error='')
def finished(app):return not app.friend_auth['running'] and not app.friends.job['running'] and app.friend_auth['phase']=='done'
with patch.object(f,'fetch_family',family),patch.object(lib,'request_api',api),patch.object(f.App,'fetch_prices',priced):
    app=f.App()
    with patch.object(app.browser_auth,'request',side_effect=AssertionError('must not reopen login')):
        assert app.start_token(token)[0];wait(lambda:finished(app))
        assert app.friends.job['phase']=='done',app.status()
        assert len(app.friends.data['people'])==2 and all(p['status']=='ok' for p in app.friends.data['people'])
        assert any(call[0]=='IPlayerService/GetPlayerLinkDetails/v1' for call in seen)
        assert len(store)>=2 and app.prices['cn:10']['initial']==5000
        assert token not in json.dumps(app.status()) and 'must-never-leak' not in json.dumps(app.status())
        assert app.start(False)[0];wait(lambda:finished(app) and app.phase=='idle')
        assert app.friends.start({'action':'sync','ids':[sid,other],'region':'hk'})[0]
        wait(lambda:not app.friends.job['running']);assert store[-1][0]=='hk'
        assert app.friends.start({'action':'prices','ids':[sid,other]})[0]
        wait(lambda:not app.friends.job['running']);assert app.friends.job['phase']=='done'
    app.disconnect();calls=[]
    def browser(kind,*args):calls.append(kind);return token if kind=='token' else 'a'*32
    with patch.object(app.browser_auth,'request',browser):
        assert app.start(True)[0];wait(lambda:finished(app))
        assert calls==['token','key'],calls
        assert app.start(False)[0];wait(lambda:finished(app) and app.phase=='idle');assert calls==['token','key']
    # A family failure must still run the entire friends pipeline.
    app.disconnect()
    with patch.object(f,'fetch_family',side_effect=f.SteamFailure('当前账号尚未加入家庭。')):
        assert app.start_token(token)[0];wait(lambda:finished(app))
        assert app.phase=='error' and app.friends.job['phase']=='done'
    # List permission errors are explicit; retry after supplementing a key needs no login.
    def denied(method,cred,**kw):
        if '.' in cred:raise lib.FriendError('权限不足，可补充 API Key，无需再次登录。','permission')
        return api(method,cred,**kw)
    with patch.object(lib,'request_api',denied):
        assert app.friends.start({'action':'list'})[0];wait(lambda:not app.friends.job['running'])
        assert app.friends.job['error_code']=='permission' and app.token==token
        assert app.friends.start({'action':'list','key':'a'*32})[0];wait(lambda:not app.friends.job['running'])
        assert app.friends.job['phase']=='done' and not app.friends.job['error']
    # Avatar service failure cannot prevent game library reads.
    def no_profile(method,*a,**kw):
        if 'GetPlayerLinkDetails' in method:raise lib.FriendError('avatar denied','permission')
        return api(method,*a,**kw)
    app.friends.key=None
    with patch.object(lib,'request_api',no_profile):
        assert app.friends.start({'action':'list'})[0];wait(lambda:not app.friends.job['running'])
        assert all(p['status']=='ok' for p in app.friends.data['people'])
    # Token changes cancel an old response before it can overwrite a new account.
    entered=threading.Event();release=threading.Event()
    def blocked(*a,**kw):entered.set();release.wait(2);return api(*a,**kw)
    with patch.object(lib,'request_api',blocked):
        assert app.friends.start({'action':'list'})[0];assert entered.wait(1)
        app.friends.use_token(jwt(other),other);release.set();wait(lambda:not app.friends.job['running'])
        assert app.friends.data['account_id']==other and not app.friends.data['people']
    app.disconnect();app.browser_auth.close()
print('PASS token/Key API contracts, backend list→libraries→prices, manual retry, one QR login, permission errors, no-family, account cancellation and redaction')
