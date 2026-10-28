from pathlib import Path
import sys,threading,json,copy,base64
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
from family_dashboard_server import App
app=App();base=app.status();app.browser_auth.close()
root=str(Path(__file__).resolve().parents[1] / 'web')
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=root,**kw)
 def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
url=f'http://127.0.0.1:{server.server_address[1]}'
class Fixture:
 def __init__(self,n=205):
  self.s=copy.deepcopy(base);self.calls=[];self.active=None;self.reads=0
  self.ps=[{'steamid':str(76561198000000000+i),'personaname':f'Friend {i:03d}','status':'pending','is_self':i==0,'games':[]} for i in range(n)]
  self.s['friends']={'data':{'account_id':self.ps[0]['steamid'],'people':self.ps,'fetched_at':'2026-10-28T00:00:00Z'},'job':{'running':False,'message':f'已加载 {n-1} 位好友，勾选后同步游戏库。'},'revision':1,'key_configured':False,'auth_configured':True,'auth':{'running':False,'phase':'done'}}
  self.s['session']={'connected':True,'phase':'idle','message':'已同步最新家庭库。'}
 def route(self,r):
  path=r.request.url.split('/api/')[-1]
  if path=='session':r.fulfill(status=201,json={'session':'synthetic'});return
  if path=='state':
   if self.active:
    self.reads+=1
    if self.reads>=3:
     kind,ids=self.active
     if kind=='sync':
      for sid in ids:
       p=next(p for p in self.ps if p['steamid']==sid);i=self.ps.index(p)
       p.update(status='unavailable' if i==1 else 'ok',games=[] if i==1 else [{'appid':1000+i,'name':'Synthetic','playtime_forever':i*60}])
      self.s['friends']['revision']+=1;self.s['friends']['job'].update(running=False,done=len(ids),message='同步完成')
     else:
      for sid in ids:
       for g in next(p for p in self.ps if p['steamid']==sid)['games']:
        self.s['prices']['cn:'+str(g['appid'])]={'known':True,'initial':g['appid']*100,'final':100,'checked':1,'currency':'CNY'}
      self.s['price_job'].update(running=False,message='商店价格查询完成')
     self.active=None
   r.fulfill(json=self.s);return
  if path=='friends':
   b=r.request.post_data_json;assert b['action']=='sync' and len(b['ids'])<=100 and self.active is None
   self.active=('sync',b['ids']);self.calls.append(self.active);self.reads=0
   self.s['friends']['job']={'running':True,'done':0,'message':'正在同步'}
  elif path=='prices':
   b=r.request.post_data_json;assert b['scope']=='friends' and self.active is None
   self.active=('prices',b['ids']);self.calls.append(self.active);self.reads=0;self.s['price_job']={'running':True,'scope':'friends','message':'正在估值'}
  elif path=='refresh':self.calls.append(('refresh',[]))
  else:raise AssertionError(path)
  r.fulfill(status=202,json={'message':'OK'})
with sync_playwright() as p:
 b=p.chromium.launch(headless=True,channel='chromium');f=Fixture();pg=b.new_page(viewport={'width':1440,'height':1000})
 errors=[];pg.on('pageerror',lambda e:errors.append(str(e)));pg.route('**/api/**',f.route)
 pg.add_init_script('const nativeTimeout=window.setTimeout;window.setTimeout=(fn,ms,...a)=>nativeTimeout(fn,ms===1200||ms===12000?35:ms,...a)')
 pg.goto(url+'/#overview');pg.wait_for_function("friendHub.debug().flow.phase==='done'&&!friendHub.running",timeout=30000)
 assert pg.evaluate('friendHub.workspace')=='family'
 assert [len(ids) for kind,ids in f.calls if kind=='sync']==[100,100,5]
 assert [len(ids) for kind,ids in f.calls if kind=='prices']==[99,100,5]
 assert pg.locator('.nav a').all_text_contents()==['家庭贡献','好友收藏','SteamDB价格']
 assert pg.locator('[data-fh-tab=steamdb]').count()==0
 assert pg.locator('.family-sections').is_visible()
 pg.locator('[data-friends-nav]').click();assert pg.locator('#fhTabs').is_visible()
 assert len(pg.evaluate('friendHub.debug().chosen'))==205
 assert pg.locator('.nav a.active').inner_text()=='好友收藏'
 count=len(f.calls)
 pg.locator('[data-db-open]').click();assert pg.locator('#fhTabSteamdb').is_visible() and not pg.locator('#fhTabs').is_visible()
 assert '成员自动同步' in pg.locator('.db-source-note').inner_text()
 assert '205' in pg.locator('#dbSummary').inner_text()
 assert not pg.locator('dialog[open]').count()
 pg.locator('[data-space=friends]').click();pg.locator('[data-fh-tab=compare]').click();assert pg.locator('#fhTabCompare').is_visible()
 pg.locator('[data-space=family]').click();assert pg.locator('.family-sections').is_visible()
 assert len(f.calls)==count
 pg.locator('#refreshBtn').click();assert f.calls[-1][0]=='refresh'
 pg.locator('[data-space=friends]').click();pg.locator('#refreshBtn').click();assert f.calls[-1][0]=='refresh'
 for width in [1440,390]:
  pg.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
  for name in ['family','friends','steamdb']:
   pg.locator('[data-db-open]' if name=='steamdb' else f'[data-space={name}]').click()
   assert pg.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,name)
 assert not errors,errors
 assert pg.evaluate('localStorage.length')==0 and pg.evaluate('sessionStorage.length')==0
 b.close()
server.shutdown()
print('PASS background auto-sync from family page, 205 selected, 100/100/5 batches, prices queue, three-item sidebar, separate subtabs, shared refresh, no relogin on switching, desktop/mobile and no storage/JS errors')
