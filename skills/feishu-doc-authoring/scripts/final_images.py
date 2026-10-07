#!/usr/bin/env python3
"""贴图终版: 滚动加载全文 -> 锚点定位贴图; 兜底=文末追加. 用法: final_images.py"""
import json, time, socket, base64, os, struct, subprocess, sys
import urllib.request as ur
CDP='http://127.0.0.1:9228'
ENV={**os.environ,'DISPLAY':':1'}
def xdo(*a): return subprocess.run(['xdotool',*a],env=ENV,capture_output=True,timeout=15)
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
def find_tab(key):
    with ur.urlopen(CDP+'/json/list',timeout=10) as r:
        for t in json.loads(r.read()):
            if t.get('type')=='page' and key in (t.get('url') or ''): return t

class Doc:
    def __init__(self, key):
        self.tab=find_tab(key); self.ws=WS(self.tab['webSocketDebuggerUrl']); self.mid=[0]
    def cmd(self,m,p=None):
        self.mid[0]+=1; self.ws.send({"id":self.mid[0],"method":m,"params":p or {}}); return self.mid[0]
    def ev(self,expr,timeout_s=25):
        myid=self.cmd("Runtime.evaluate",{"expression":expr,"returnByValue":True,"awaitPromise":True})
        dl=time.time()+timeout_s
        while time.time()<dl:
            try: m=self.ws.recv()
            except socket.timeout: break
            if m.get('id')==myid: return m.get('result',{})
        return {}
    def click(self,x,y):
        self.cmd("Input.dispatchMouseEvent",{"type":"mousePressed","x":x,"y":y,"button":"left","clickCount":1})
        time.sleep(0.1)
        self.cmd("Input.dispatchMouseEvent",{"type":"mouseReleased","x":x,"y":y,"button":"left","clickCount":1})
    def activate(self):
        self.cmd("Page.bringToFront"); time.sleep(1.5)
        r=self.ev('JSON.stringify({v:document.visibilityState})')
        if 'visible' not in r.get('result',{}).get('value',''): return False
        wins=xdo('search','--onlyvisible','--name','Google Chrome').stdout.split()
        if wins: xdo('windowactivate','--sync',wins[0]); time.sleep(0.8)
        r=self.ev('JSON.stringify({w:innerWidth,h:innerHeight})')
        vp=json.loads(r.get('result',{}).get('value','{"w":1440,"h":900}'))
        self.click(int(vp['w'])//2, int(vp['h'])//2); time.sleep(1)
        return True
    def scroll_load_all(self):
        """键盘 End/PageDown 数轮, 迫使飞书加载全部块"""
        for i in range(10):
            xdo('key','End'); time.sleep(0.7)
        for i in range(3):
            xdo('key','PageUp'); time.sleep(0.4)
        time.sleep(1)
    def find_anchor(self, text):
        r=self.ev(f'''(() => {{
          const root=document.querySelector('.page-block.root-block')||document.body;
          const w=document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n;
          while(n=w.nextNode()){{ if(n.textContent.includes('{text}')){{
            const el=n.parentElement; el.scrollIntoView({{block:'center'}});
            const rc=el.getBoundingClientRect();
            return JSON.stringify({{x:Math.round(rc.left+Math.min(rc.width/2,300)), y:Math.round(rc.top+rc.height/2),
              vis: rc.top>60 && rc.bottom<innerHeight-60}});
          }} }}
          return 'NOT_FOUND';
        }})()''')
        v=r.get('result',{}).get('value','')
        return v
    def paste_img_selected_line(self, imgfile):
        xdo('key','Home'); time.sleep(0.3); xdo('key','shift+End'); time.sleep(0.4)
        subprocess.run(['xclip','-selection','clipboard','-t','image/png','-i',imgfile],env=ENV,timeout=10)
        time.sleep(0.8); xdo('key','ctrl+v'); time.sleep(4)
    def paste_img_at_end(self, imgfile):
        xdo('key','ctrl+End'); time.sleep(0.8)
        xdo('key','Return'); time.sleep(0.8)
        subprocess.run(['xclip','-selection','clipboard','-t','image/png','-i',imgfile],env=ENV,timeout=10)
        time.sleep(0.8); xdo('key','ctrl+v'); time.sleep(4)
    def imgs(self):
        r=self.ev('''(() => { const root=document.querySelector('.page-block.root-block')||document.body;
          return root.querySelectorAll('img').length; })()''')
        try: return int(r.get('result',{}).get('value','0'))
        except Exception: return -1

LOG=open('/tmp/feishu_doc_work/final_images.log','w',encoding='utf-8')
def log(*a):
    print(*a,flush=True); LOG.write(' '.join(map(str,a))+'\n'); LOG.flush()
W='/tmp/feishu_doc_work'
tasks=[('DCr5wzIfOi9EOOkECR9c4ekZnhb','图2_五条防护规则与缺陷率',f'{W}/assets/chart_rules.png','B_rules'),
       ('Eu0dw9DPOiJmPjkQoqecrZRyn8e','图3_人机协作双三角',f'{W}/assets/chart_dualtriangle.png','A_tri')]
for key,anchor,img,tag in tasks:
    d=Doc(key)
    log(tag,'activate:', d.activate())
    d.scroll_load_all()
    v=d.find_anchor(anchor)
    log(tag,'anchor:', v[:100])
    if v!='NOT_FOUND':
        try:
            c=json.loads(v)
            d.click(c['x'],c['y']); time.sleep(1)
            d.paste_img_selected_line(img)
            log(tag,'inline pasted, imgs=', d.imgs())
        except Exception as e:
            log(tag,'inline fail',e,'-> 文末兜底'); d.paste_img_at_end(img); log(tag,'end imgs=', d.imgs())
    else:
        log(tag,'锚点未加载/不存在 -> 文末兜底')
        d.paste_img_at_end(img)
        log(tag,'end imgs=', d.imgs())
    # 终验截图
    myid=d.cmd("Page.captureScreenshot", {"format":"png"})
    dl=time.time()+20
    while time.time()<dl:
        try: m=d.ws.recv()
        except socket.timeout: break
        if m.get('id')==myid:
            dd=m.get('result',{}).get('data')
            if dd: open(f'{W}/{tag}_final.png','wb').write(base64.b64decode(dd)); log(tag,'shot saved')
            break
log('IMAGES_DONE')
