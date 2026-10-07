#!/usr/bin/env python3
"""v4: 点击文档可视区中心(真人行为) -> 备份 -> 全选替换粘贴 -> 严格过滤点转换 -> 验证"""
import json, time, socket, base64, os, struct, subprocess, sys
import urllib.request as ur

CDP = 'http://127.0.0.1:9228'
DOC_KEY, MD_FILE, BACKUP, LOGP = sys.argv[1:5]
LOG = open(LOGP, 'w', encoding='utf-8')
def log(*a):
    print(*a, flush=True); LOG.write(' '.join(map(str,a))+'\n'); LOG.flush()

def find_tab():
    with ur.urlopen(CDP + '/json/list', timeout=10) as r:
        for t in json.loads(r.read()):
            if t.get('type')=='page' and DOC_KEY in (t.get('url') or ''): return t
    return None

class WS:
    def __init__(self, url):
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
log('TAB:', tab['id'])
ws=WS(tab['webSocketDebuggerUrl'])
mid=[0]
def send_cmd(method, params=None):
    mid[0]+=1
    ws.send({"id":mid[0],"method":method,"params":params or {}})
    return mid[0]
def ev(expr, timeout_s=30):
    myid=send_cmd("Runtime.evaluate", {"expression":expr,"returnByValue":True,"awaitPromise":True})
    dl=time.time()+timeout_s
    while time.time()<dl:
        try: m=ws.recv()
        except socket.timeout: break
        if m.get('id')==myid: return m.get('result',{})
    return {'error':'timeout'}
def click(x, y):
    send_cmd("Input.dispatchMouseEvent", {"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
    time.sleep(0.1)
    send_cmd("Input.dispatchMouseEvent", {"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})

ENV={**os.environ,'DISPLAY':':1'}
def xdo(*a): return subprocess.run(['xdotool',*a],env=ENV,capture_output=True,timeout=15)

# 0 置前+激活(真调用bringToFront!)
send_cmd("Page.bringToFront")
time.sleep(1.5)
r=ev('JSON.stringify({vis:document.visibilityState, focus:document.hasFocus(), url:location.href.slice(0,80)})')
log('STEP0a visibility:', r.get('result',{}).get('value'))
try:
    vd=json.loads(r.get('result',{}).get('value','{}'))
    if vd.get('vis')!='visible' or not vd.get('focus'):
        log('ABORT: tab不可见或无焦点, 键盘事件会落错窗口'); sys.exit(4)
except Exception as e:
    log('ABORT: visibility解析失败', e); sys.exit(4)
wins=xdo('search','--onlyvisible','--name','Google Chrome').stdout.split()
if wins: xdo('windowactivate','--sync',wins[0]); time.sleep(1)
log('STEP0 active:', subprocess.run(['xdotool','getactivewindow','getwindowname'],env=ENV,capture_output=True,text=True).stdout.strip())

# 1 视口尺寸
r=ev('JSON.stringify({w:innerWidth,h:innerHeight,scrollY:scrollY})')
try: vp=json.loads(r.get('result',{}).get('value','{"w":1440,"h":900}'))
except Exception: vp={"w":1440,"h":900}
log('STEP1 viewport:', vp)
cx, cy = int(vp['w']*0.5), int(vp['h']*0.45)

# 2 点击文档可视区中心(2次确保进入编辑态)
click(cx, cy); time.sleep(1.0)
click(cx, cy); time.sleep(1.0)

# 3 焦点检查
r=ev('''(() => {
  const zone=document.querySelector('.zone-container.text-editor');
  const ae=document.activeElement;
  const sel=window.getSelection();
  const anchorIn = sel.anchorNode && zone ? zone.contains(sel.anchorNode.nodeType==1?sel.anchorNode:sel.anchorNode.parentNode) : false;
  return JSON.stringify({aeTag:ae?ae.tagName:null, aeCls:(ae&&ae.className||'').toString().slice(0,40), inZone: zone?(zone===ae||zone.contains(ae)):false, anchorIn});
})()''')
log('STEP3 focus:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False))

# 4 备份(现有内容,无论焦点)
r=ev('JSON.stringify({t:document.title.slice(0,50), x:(document.querySelector(".zone-container.text-editor")||{}).innerText ? document.querySelector(".zone-container.text-editor").innerText.length : 0})')
log('STEP4 size:', r.get('result',{}).get('value'))
r=ev('JSON.stringify({title:document.title, text:(document.querySelector(".zone-container.text-editor")||{innerText:""}).innerText})')
try:
    d=json.loads(r.get('result',{}).get('value','{}'))
    open(BACKUP,'w',encoding='utf-8').write(d.get('text',''))
    log('STEP4 backup chars=', len(d.get('text','')))
except Exception as e:
    log('STEP4 backup FAIL:', e); sys.exit(3)

# 5 光标到文末 + 全选(受信任)
xdo('key','ctrl+End'); time.sleep(0.6)
xdo('key','ctrl+a'); time.sleep(0.8)

# 6 剪贴板+粘贴
md=open(MD_FILE,encoding='utf-8').read()
p=subprocess.run(['xclip','-selection','clipboard','-i'],input=md.encode(),env=ENV,timeout=10)
log('STEP6 clip rc=',p.returncode,'chars=',len(md))
xdo('key','ctrl+v'); time.sleep(4)

# 7 截图(点转换前留证)
myid=send_cmd("Page.captureScreenshot", {"format":"png"})
dl=time.time()+25
while time.time()<dl:
    try: m=ws.recv()
    except socket.timeout: break
    if m.get('id')==myid:
        d=m.get('result',{}).get('data')
        if d: open(LOGP.replace('.log','_shot.png'),'wb').write(base64.b64decode(d)); log('STEP7 shot saved')
        break

# 8 探测+严格过滤点转换
r=ev('''(() => {
  const cands=[...document.querySelectorAll('button, [role=button]')].filter(b=>{
    const t=(b.textContent||'').trim();
    const vis=b.offsetWidth>0 && b.offsetHeight>0;
    return vis && t.length>0 && t.length<15 && (t.includes('转换')||t.includes('一键'));
  }).map(b=>b.textContent.trim());
  const zone=document.querySelector('.zone-container.text-editor');
  return JSON.stringify({cands, len: zone?zone.textContent.length:0, md:(zone?zone.textContent:'').slice(0,50)});
})()''')
log('STEP8 probe:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False)[:300])

r=ev('''(() => {
  const btn=[...document.querySelectorAll('button, [role=button]')].find(b=>{
    const t=(b.textContent||'').trim();
    return b.offsetWidth>0 && t.length>0 && t.length<15 && (t.includes('转换')||t.includes('一键'));
  });
  if(btn){btn.click(); return 'CLICKED:'+btn.textContent.trim();}
  return 'NO_BTN';
})()''')
log('STEP9:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False))
time.sleep(4)

# 10 终验
r=ev('''(() => {
  const zone=document.querySelector('.zone-container.text-editor');
  const txt=zone?zone.textContent:'';
  return JSON.stringify({len:txt.length, mdMarks:(txt.match(/##/g)||[]).length, pipes:(txt.match(/\\|/g)||[]).length, head:txt.slice(0,50)});
})()''')
log('STEP10 verify:', json.dumps(r.get('result',{}).get('value',r),ensure_ascii=False)[:300])
log('V4_DONE')
