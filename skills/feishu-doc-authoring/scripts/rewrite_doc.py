#!/usr/bin/env python3
"""终版: 迭代清空(ctrl+a+Delete循环, 克服虚拟渲染) -> 粘贴全文 -> 可选贴图. 用法: rewrite_doc.py <docKey> <mdFile> <log> [img1.png,img2.png]"""
import json, time, socket, base64, os, struct, subprocess, sys
import urllib.request as ur
CDP='http://127.0.0.1:9228'
DOC_KEY, MD_FILE, LOGP = sys.argv[1], sys.argv[2], sys.argv[3]
IMGS = sys.argv[4].split(',') if len(sys.argv)>4 else []
IMG_MARKS = json.load(open('img_marks.json',encoding='utf-8')) if os.path.exists('img_marks.json') else {}
LOG=open(LOGP,'w',encoding='utf-8')
def log(*a):
    print(*a,flush=True); LOG.write(' '.join(map(str,a))+'\n'); LOG.flush()
def find_tab():
    with ur.urlopen(CDP+'/json/list',timeout=10) as r:
        for t in json.loads(r.read()):
            if t.get('type')=='page' and DOC_KEY in (t.get('url') or ''): return t
class WS:
    def __init__(self,url):
        rest=url[5:]; hp,path=rest.split('/',1); path='/'+path
        host,port=hp.split(':') if ':' in hp else (hp,80)
        self.sock=socket.create_connection((host,int(port)),timeout=40)
        key=base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {hp}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp=b''
        while b'\r\n\r\n' not in resp:
            c=self.sock.recv(4096)
            if not c: raise RuntimeError('EOF')
            resp+=c
    def send(self,obj):
        data=json.dumps(obj).encode(); h=bytearray([0x81]); n=len(data)
        if n<126: h.append(0x80|n)
        elif n<65536: h+=bytes([0x80|126])+struct.pack('>H',n)
        else: h+=bytes([0x80|127])+struct.pack('>Q',n)
        mask=os.urandom(4); h+=mask
        self.sock.sendall(bytes(h)+bytes(b^mask[i%4] for i,b in enumerate(data)))
    def recv(self):
        def rd(n):
            b=b''
            while len(b)<n:
                c=self.sock.recv(n-len(b))
                if not c: raise EOFError()
                b+=c
            return b
        b1,b2=rd(2); n=b2&0x7f
        if n==126: n=struct.unpack('>H',rd(2))[0]
        elif n==127: n=struct.unpack('>Q',rd(8))[0]
        if b2&0x80: rd(4)
        return json.loads(rd(n).decode('utf-8',errors='ignore'))
tab=find_tab()
if not tab: log('TAB_NOT_FOUND'); sys.exit(1)
ws=WS(tab['webSocketDebuggerUrl']); mid=[0]
def cmd(m,p=None):
    mid[0]+=1; ws.send({"id":mid[0],"method":m,"params":p or {}}); return mid[0]
def ev(expr,timeout_s=25):
    myid=cmd("Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":True})
    dl=time.time()+timeout_s
    while time.time()<dl:
        try: m=ws.recv()
        except socket.timeout: break
        if m.get('id')==myid: return m.get('result',{})
    return {}
def click(x,y):
    cmd("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
    time.sleep(0.1)
    cmd("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})
ENV={**os.environ,'DISPLAY':':1'}
def xdo(*a): return subprocess.run(['xdotool',*a],env=ENV,capture_output=True,timeout=15)

def bodylen():
    r=ev('document.body.innerText.length')
    v=r.get('result',{}).get('value')
    try: return int(v)
    except Exception: return -1

# 0 bringToFront + 激活 + 可见性断言
cmd("Page.bringToFront"); time.sleep(1.5)
r=ev('JSON.stringify({v:document.visibilityState,f:document.hasFocus()})')
vd=json.loads(r.get('result',{}).get('value','{}'))
log('STEP0:', vd)
if vd.get('v')!='visible': log('ABORT invisible'); sys.exit(4)
wins=xdo('search','--onlyvisible','--name','Google Chrome').stdout.split()
if wins: xdo('windowactivate','--sync',wins[0]); time.sleep(0.8)

# 1 点中心聚焦
r=ev('JSON.stringify({w:innerWidth,h:innerHeight})')
vp=json.loads(r.get('result',{}).get('value','{"w":1440,"h":900}'))
cx,cy=int(vp['w']*0.5),int(vp['h']*0.45)
click(cx,cy); time.sleep(1)

# 2 备份
r=ev('(document.querySelector(".zone-container.text-editor")||{innerText:""}).innerText')
old=r.get('result',{}).get('value','')
open(LOGP.replace('.log','_backup.txt'),'w',encoding='utf-8').write(old or '')
log('STEP2 backup chars=',len(old or ''))

# 3 迭代清空: ctrl+a + Delete, 直到 bodyLen 稳定在低位
before=bodylen(); log('STEP3 clear start len=',before)
for i in range(8):
    xdo('key','ctrl+a'); time.sleep(0.6)
    xdo('key','Delete'); time.sleep(1.5)
    now=bodylen()
    log(f'  round{i}: {now}')
    if now < 320 or now >= before:  # 空了 or 删不动了
        if now < 320: break
        if i>=1 and now>=before: log('  删不动,停'); break
    before=now
r=ev('''(() => { const it=document.body.innerText;
  return JSON.stringify({len:it.length, sample:it.slice(0,120)}); })()''')
log('STEP3 after clear:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False)[:250])

# 4 粘贴全文
md=open(MD_FILE,'rb').read()
p=subprocess.run(['xclip','-selection','clipboard','-i'],input=md,env=ENV,timeout=10)
log('STEP4 clip rc=',p.returncode,'chars=',len(md))
xdo('key','ctrl+v'); time.sleep(5)
r=ev('''(() => { const it=document.body.innerText;
  return JSON.stringify({len:it.length, pipes:(it.match(/\\|/g)||[]).length, mdMarks:(it.match(/##/g)||[]).length,
    markers:(it.match(/图\\d_/g)||[]).length, head:it.slice(0,60)}); })()''')
log('STEP4 after paste:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False)[:250])

# 5 贴图: 标记行(body级搜索)
for i, imgf in enumerate(IMGS, 1):
    mark=None
    for k,v in IMG_MARKS.items():
        if v==i-0 or k.startswith(f'图{i}_'): mark=k; break
    if not mark: log(f'STEP5.{i} 无标记映射,跳过'); continue
    r=ev(f'''(() => {{
      const w=document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT); let n;
      while(n=w.nextNode()){{ if(n.textContent.includes('{mark}')){{
        const el=n.parentElement; el.scrollIntoView({{block:'center'}});
        const rc=el.getBoundingClientRect();
        return JSON.stringify({{x:Math.round(rc.left+Math.min(rc.width/2,300)), y:Math.round(rc.top+rc.height/2),
          vis: rc.top>60 && rc.bottom<innerHeight-60}});
      }} }}
      return 'NOT_FOUND';
    }})()''')
    v=r.get('result',{}).get('value','')
    log(f'STEP5.{i}', mark, '->', v[:100])
    if v=='NOT_FOUND': continue
    c=json.loads(v)
    if not c.get('vis'): log('  行不可见,滚动后重试'); continue
    click(c['x'],c['y']); time.sleep(1)
    xdo('key','Home'); time.sleep(0.4); xdo('key','shift+End'); time.sleep(0.4)
    p=subprocess.run(['xclip','-selection','clipboard','-t','image/png','-i',imgf],env=ENV,timeout=10)
    log('  png rc=',p.returncode, os.path.basename(imgf))
    time.sleep(0.8); xdo('key','ctrl+v'); time.sleep(4)
    r=ev('JSON.stringify({imgs:document.querySelectorAll("img").length, left:document.body.innerText.includes("'+mark+'")})')
    log('  after:', r.get('result',{}).get('value'))

# 6 终验+截图
r=ev('''(() => { const it=document.body.innerText;
  return JSON.stringify({len:it.length, pipes:(it.match(/\\|/g)||[]).length, mdMarks:(it.match(/##/g)||[]).length,
    markers:(it.match(/图\\d_/g)||[]).length, imgs:document.body.querySelectorAll("img").length}); })()''')
log('STEP6 final:', r.get('result',{}).get('value'))
myid=cmd("Page.captureScreenshot", {"format":"png"})
dl=time.time()+25
while time.time()<dl:
    try: m=ws.recv()
    except socket.timeout: break
    if m.get('id')==myid:
        d=m.get('result',{}).get('data')
        if d: open(LOGP.replace('.log','_shot.png'),'wb').write(base64.b64decode(d)); log('STEP7 shot')
        break
log('DOC_DONE')
