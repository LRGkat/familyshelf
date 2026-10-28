"""Achievement edge cases through the real JSON parser; no live credentials."""
import json
import sys
import threading
import urllib.parse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import friend_library as lib
SID='76561198000000000'
GAMES=[{'appid':i,'name':f'Game {i}','playtime_forever':60} for i in range(1,103)]
seen=[]
mode={'value':'ok'}
class Response:
    headers={}
    def __init__(self,payload):self.payload=payload
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.payload).encode()
def upstream(request,**kwargs):
    assert request.get_method() == 'POST', 'Steam requires POST; GET returns HTTP 405'
    assert not urllib.parse.urlsplit(request.full_url).query, 'credentials belong in the form body'
    assert request.get_header('Content-type') == 'application/x-www-form-urlencoded'
    q=urllib.parse.parse_qs(request.data.decode('utf-8'))
    assert q.get('access_token') == ['synthetic.token.value'] or q.get('key') == ['a'*32]
    data=json.loads(q['input_json'][0]);seen.append(data)
    assert data['include_unvetted_apps'] is True and data['steamid']==SID
    entries=[{'appid':a,'total':10,'unlocked':10,'all_unlocked':True} for a in data['appids']]
    if mode['value']=='mixed':
        entries=[{'appid':1,'total':1000,'unlocked':999,'percentage':100,'all_unlocked':True},
                 {'appid':2,'total':0,'unlocked':0,'all_unlocked':True},
                 {'appid':3,'total':10,'unlocked':10,'all_unlocked':True}]
    if mode['value']=='omitted':entries=entries[:1]
    if mode['value']=='invalid':entries=[{'appid':1,'total':True,'unlocked':1}]
    if mode['value']=='duplicate':entries=[entries[0],entries[0]]
    if mode['value']=='private':return Response({'response':{}})
    return Response({'response':{'achievement_progress':entries}})
with patch.object(lib.urllib.request,'urlopen',upstream):
    result=lib.achievement_progress('a'*32,SID,GAMES,threading.Event())
    assert [len(x['appids']) for x in seen]==[100,2]
    assert result['achievement_status']=='ok' and len(result['achievement_games'])==102
    assert all(g['complete'] for g in result['achievement_games'])
    mode['value']='mixed'
    result=lib.achievement_progress('synthetic.token.value',SID,GAMES[:3],threading.Event())
    assert [g['complete'] for g in result['achievement_games']]==[False,False,True]
    mode['value']='omitted'
    result=lib.achievement_progress('synthetic.token.value',SID,GAMES[:3],threading.Event())
    assert result['achievement_status']=='partial' and result['achievement_unknown']==2
    for invalid in ['invalid','duplicate','private']:
        mode['value']=invalid
        try:lib.achievement_progress('a'*32,SID,GAMES[:3],threading.Event());raise AssertionError(invalid)
        except lib.FriendError as error:assert error.code.startswith('achievement_')
    cancel=threading.Event();cancel.set();before=len(seen)
    assert lib.achievement_progress('a'*32,SID,GAMES,cancel) is None and len(seen)==before
# Old successful snapshots survive failed refreshes and are removed from rankings.
friends=lib.Friends(Path('.'));friends.data={'account_id':SID,'people':[
    {'steamid':SID,'games':GAMES[:1],'status':'ok','achievement_fetched_at':'yesterday','achievement_games':[{'appid':1,'total':10,'unlocked':10}]},
    {'steamid':'76561198000000001','games':GAMES[:1],'status':'ok','achievement_fetched_at':'yesterday'}]}
with patch.object(lib,'request_api',side_effect=lib.FriendError('限流','rate_limit')):
    friends.sync_achievements([p['steamid'] for p in friends.data['people']],'synthetic.token.value',friends.cancel)
assert [p['achievement_status'] for p in friends.data['people']]==['stale','stale']
assert friends.data['people'][0]['achievement_games'][0]['unlocked']==10
assert all(p['achievement_error_code']=='rate_limit' for p in friends.data['people'])
print('PASS achievements: required POST form with token/Key, bulk JSON, batching, rounded 100%, no-achievement games, missing/private/malformed, cancellation and stale/rate-limit isolation')
