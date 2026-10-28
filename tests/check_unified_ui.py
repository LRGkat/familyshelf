"""Browser→real cloud handler→worker integration with synthetic Steam responses."""
from pathlib import Path
import sys,threading,json,base64,time
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
import cloud_server as cloud
import family_dashboard_server as f
import friend_library as lib
sid='76561198000000000';friend='76561198000000001';private='76561198000000002'
token='eyJhbGciOiJSUzI1NiJ9.'+base64.urlsafe_b64encode(json.dumps({'sub':sid,'exp':time.time()+600}).encode()).decode().rstrip('=')+'.synthetic'
mode={'denied':False};calls=[]
def api(method,cred,**kw):
 calls.append(method)
 if 'GetFriendsList' in method:
  if mode['denied']:raise lib.FriendError('Steam 拒绝好友接口权限；可补充 API Key，无需再次登录。','permission')
  return {'response':{'friendslist':{'friends':[{'ulfriendid':s,'efriendrelationship':3} for s in [friend,private,'103582791429521412']]}}}
 if 'GetPlayerLinkDetails' in method:return {'response':{'accounts':[{'public_data':{'steamid':s,'persona_name':'Friend '+s[-1]}} for s in kw['steamids']]}}
 if kw['steamid']==private:return {'response':{}}
 return {'response':{'game_count':1,'games':[{'appid':10 if kw['steamid']==sid else 20,'name':'Synthetic game','playtime_forever':120}]}}
def family(token,progress):return f.normalize({'steamid':sid,'members':[{'steamid':sid}],'games':[]})
def priced(self,region,apps,cancel):
 with self.lock:
  for aid in apps:self.prices[f'{region}:{aid}']={'known':True,'checked':time.time(),'initial':1000 if aid==10 else 2000,'final':500,'currency':f.REGIONS[region]}
  self.price_job.update(running=False,done=len(apps),message='商店价格查询完成',error='')
api_server=ThreadingHTTPServer(('127.0.0.1',0),cloud.Handler)
apiurl=f'http://127.0.0.1:{api_server.server_address[1]}'
root=str(Path(__file__).resolve().parents[1] / 'web')
class Handler(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=root,**kw)
 def log_message(self,*a):pass
 def do_GET(self):
  if self.path=='/config.js':
   raw=f'window.FAMILYSHELF_CONFIG={{apiBase:"{apiurl}"}}'.encode();self.send_response(200);self.send_header('Content-Type','application/javascript');self.end_headers();self.wfile.write(raw);return
  super().do_GET()
static=ThreadingHTTPServer(('127.0.0.1',0),Handler);url=f'http://127.0.0.1:{static.server_address[1]}'
cloud.ORIGINS={url}
for s in [api_server,static]:threading.Thread(target=s.serve_forever,daemon=True).start()
with patch.object(lib,'request_api',api),patch.object(f,'fetch_family',family),patch.object(f.App,'fetch_prices',priced),sync_playwright() as p:
 browser=p.chromium.launch(headless=True,channel='chromium');ctx=browser.new_context(viewport={'width':1440,'height':1000})
 # Another tab holds the obsolete lock throughout: it must not stall this visitor.
 holder=ctx.new_page();holder.goto(url);holder.evaluate("navigator.locks.request('familyshelf-friend-pipeline',()=>new Promise(()=>{}));true")
 page=ctx.new_page();errors=[];posts=[]
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.on('request',lambda r:posts.append(r.url.split('/api/')[-1]) if '/api/' in r.url and r.method=='POST' else None)
 page.add_init_script('const nativeTimeout=window.setTimeout;window.setTimeout=(fn,ms,...a)=>nativeTimeout(fn,ms===1200||ms===12000?150:ms,...a)')
 page.goto(url+'/#overview');page.wait_for_function('backend')
 page.locator('#sideConnect').click();page.locator('#authTokenTab').click();page.locator('#steamTokenInput').fill(token);page.locator('#tokenLoginBtn').click()
 page.wait_for_function("friendHub.debug().people.length===3&&!friendHub.running&&friendHub.debug().people.some(p=>p.known>0)")
 assert page.evaluate('friendHub.workspace')=='family'
 assert 'friends' not in posts and 'prices' not in posts,posts # No front-end pipeline.
 page.locator('[data-friends-nav]').click()
 assert page.locator('.nav a').all_text_contents()==['家庭贡献','好友收藏']
 assert page.locator('#fhTabSteamdb').count()==0 and 'SteamDB' not in page.locator('body').inner_text()
 assert len(page.evaluate('friendHub.debug().chosen'))==3
 assert page.evaluate('friendHub.debug().ranked[0].steamid')==friend
 assert page.locator('#fhRows').inner_text().find('不可读取')>=0
 before=calls.count('IFriendsListService/GetFriendsList/v1')
 page.locator('#refreshBtn').click()
 page.wait_for_function('!friendHub.running');assert calls.count('IFriendsListService/GetFriendsList/v1')==before+1
 assert posts[-1]=='friends',posts
 # Manual selected synchronization is a single server request and completes pricing.
 page.locator('#fhSyncSelected').click();page.wait_for_function('!friendHub.running');assert posts[-1]=='friends'
 page.locator('#fhClearSelection').click();count=len(posts);page.locator('#fhSyncSelected').click()
 assert '请至少选择' in page.locator('#toast').inner_text() and len(posts)==count
 page.locator('#fhSelectAll').click()
 # Permission failures must override "auth shared" and expose a working retry.
 mode['denied']=True;page.locator('#fhConnect').click()
 page.wait_for_function("document.querySelector('#fhNotice').classList.contains('failed')")
 assert '权限' in page.locator('#fhNotice').inner_text() and page.locator('#fhRetry').is_visible()
 assert '同步未完成' in page.locator('#topStatus').inner_text()
 mode['denied']=False;page.locator('#fhRetry').click();page.wait_for_function("!friendHub.running&&!document.querySelector('#fhNotice').classList.contains('failed')")
 assert page.locator('#fhRetry').is_hidden()
 for width in [1440,390]:
  page.set_viewport_size({'width':width,'height':1000 if width==1440 else 844})
  for space in ['family','friends']:
   page.locator(f'[data-space={space}]').click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,space)
  for tab in ['compare','discover','manage','rank']:
   page.locator(f'[data-fh-tab={tab}]').click();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,tab)
 assert not errors,errors
 assert page.evaluate('localStorage.length')==0 and page.evaluate('sessionStorage.length')==0
 page.goto(url+'/#steamdb');page.wait_for_function('friendHub.workspace==="friends"');assert page.locator('#fhTabs').is_visible()
 browser.close()
for app,_,_ in list(cloud.SESSIONS.values()):cloud.dispose(app)
for s in [api_server,static]:s.shutdown()
print('PASS actual cloud HTTP + browser: login auto-sync with another-tab lock, manual sync/refresh, ranking/pricing, permission retry, two sections, mobile layout and no persistent storage')
