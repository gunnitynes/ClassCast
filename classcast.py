#!/usr/bin/env python3
"""
CCAST (ClassCast) — broadcast this Mac's audio to every browser on the local network.

Zero dependencies: Python 3 standard library only.
  Teacher:   http://localhost:8080/host   (opens in Chrome automatically)
  Students:  http://<this-mac's-ip>:8080   (shown big on the host page)

Audio travels teacher-browser -> student-browser directly over WebRTC
(stereo Opus, ~100 ms). This script is only the web pages plus a tiny
signalling mailbox; no audio passes through Python.

Mailbox semantics (the part that makes reconnection reliable):
  * every message gets a sequence number per recipient
  * a poll returns everything after the caller's last acknowledged seq
  * so a dropped HTTP response never loses a message
  * every poll also carries the server "generation"; if it changes the
    pages know the server was restarted and re-join by themselves
"""
import json, os, socket, subprocess, sys, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON = "--daemon" in sys.argv
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
PORT_WANTED = int(_args[0]) if _args else 8080
POLL_WAIT = 20          # seconds a long-poll blocks before returning empty
STALE = 60              # seconds without polling before a mailbox is dropped
MAX_QUEUE = 300         # per-mailbox safety cap
GEN = int(time.time())  # changes on every restart

# ----------------------------------------------------------------- mailbox
_lock = threading.Condition()
_boxes = {}             # id -> list of (seq, message)
_seq = {}               # id -> last seq assigned
_seen = {}              # id -> last poll time
_names = {}             # id -> display name (for the terminal log)
_host_hid = None        # the one studio page allowed to act as host (the most recently opened)


def _touch(cid):
    _boxes.setdefault(cid, []); _seq.setdefault(cid, 0)
    _seen[cid] = time.time()


def deliver(sender, to, data):
    with _lock:
        if to == "*":
            targets = [c for c in _boxes if c != sender]
        else:
            _touch(to); targets = [to]
        for t in targets:
            _seq[t] += 1
            _boxes[t].append((_seq[t], {"seq": _seq[t], "from": sender, "data": data}))
            del _boxes[t][:-MAX_QUEUE]
        _lock.notify_all()


def take(cid, after, wait):
    deadline = time.time() + wait
    with _lock:
        _touch(cid)
        _boxes[cid] = [(n, m) for n, m in _boxes[cid] if n > after]   # ack
        while not _boxes[cid]:
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            _lock.wait(remaining)
        return [m for _, m in _boxes[cid]]


def reset(cid):
    with _lock:
        _boxes[cid] = []; _seq[cid] = 0; _seen[cid] = time.time()


def prune():
    while True:
        time.sleep(10)
        with _lock:
            now = time.time()
            for cid in [c for c, t in _seen.items() if c != "host" and now - t > STALE]:
                _boxes.pop(cid, None); _seq.pop(cid, None); _seen.pop(cid, None)
                if cid in _names:
                    _names.pop(cid); log("listener left")


def log(msg):
    print(time.strftime("  %H:%M:%S  ") + msg, flush=True)


def lan_ips():
    """All IPv4 addresses of this Mac, default-route interface first."""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1)); ips.append(s.getsockname()[0]); s.close()
    except Exception:
        pass
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet ") and "127.0.0.1" not in line:
                ip = line.split()[1]
                if ip not in ips:
                    ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


# ----------------------------------------------------------------- pages
CSS = r"""
:root{
  --bg:#07080b;--bg2:#0d0f14;--card:#12151c;--line:#1f242e;--fg:#f4f5f7;--dim:#8b93a3;--mute:#5a6170;
  --live:#39ff88;--wait:#ffb340;--off:#ff4d5e;--accent:#8ab4ff;--ink:#062a15;
  --radius:22px;--shadow:0 20px 60px rgba(0,0,0,.45);
}
*{box-sizing:border-box}html{color-scheme:dark}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#141a26 0%,var(--bg) 60%);color:var(--fg);
 font:17px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,Roboto,sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:6px 0 18px}
.brand{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:-.02em;font-size:22px}
.brand .logo,.station .logo{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,var(--live),#1fd1ff);display:grid;place-items:center;color:#031}
.brand .logo svg,.station .logo svg{width:20px;height:20px}
.pill{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;background:var(--card);border:1px solid var(--line);color:var(--dim);font-weight:600;font-size:14px;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;background:var(--mute);flex:none}
.dot.live{background:var(--live);box-shadow:0 0 0 0 rgba(57,255,136,.6);animation:pulse 1.6s infinite}
.dot.wait{background:var(--wait)}.dot.off{background:var(--off)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(57,255,136,.55)}100%{box-shadow:0 0 0 12px rgba(57,255,136,0)}}
.card{background:linear-gradient(180deg,#141821,#0f1218);border:1px solid var(--line);border-radius:var(--radius);padding:26px;box-shadow:var(--shadow)}
.card h2{margin:0 0 6px;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim);font-weight:700}
.url{font-size:48px;font-weight:800;letter-spacing:-.03em;line-height:1.1;white-space:nowrap;margin:10px 0 14px}
.url b{color:var(--live)}
.qr{background:#fff;padding:14px;border-radius:18px;line-height:0;flex:none;justify-self:end}
.qr img,.qr canvas{display:block;width:240px;height:240px}
.alt{color:var(--dim);font-size:14px}.alt code{color:var(--fg)}
button{font:inherit;font-weight:700;border:0;border-radius:14px;padding:14px 20px;cursor:pointer;background:#222736;color:var(--fg);transition:transform .08s,filter .15s}
button:hover{filter:brightness(1.12)}button:active{transform:translateY(1px)}
button:disabled{opacity:.35;cursor:default;filter:none}
button.primary{background:var(--live);color:var(--ink);font-size:19px;padding:18px 24px;width:100%}
button.danger{background:var(--off);color:#fff}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim)}
button.icon{padding:12px 14px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.row>.grow{flex:1;min-width:160px}
select{font:inherit;width:100%;padding:13px 14px;border-radius:14px;background:#0b0d12;color:var(--fg);border:1px solid var(--line);appearance:none}
/* host */
.wrap.host{max-width:980px}.wrap.host .card{margin-bottom:18px}
.sharegrid{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:28px;align-items:center}.sharegrid>div:first-child{min-width:0}
@media(max-width:760px){.sharegrid{grid-template-columns:1fr}}
.hero{width:100%;display:flex;align-items:center;justify-content:center;gap:18px;padding:24px 28px;border-radius:20px;background:var(--live);color:var(--ink);text-align:left}
.hero svg{width:34px;height:34px;flex:none}.hero span{display:flex;flex-direction:column;line-height:1.15}
.hero b{font-size:28px;letter-spacing:-.02em}.hero small{font-size:15px;font-weight:600;opacity:.75}
.srcrow{display:flex;gap:10px;align-items:flex-end}.srcrow .grow{flex:1;min-width:0}.srcrow label{display:block;margin-bottom:8px}
.devinfo{color:var(--dim);font-size:14px;margin-top:10px;min-height:20px}.devinfo b{color:var(--fg)}
.vu{margin:22px 0 24px;padding:18px 18px 14px;border-radius:16px;background:#0a0c10;border:1px solid var(--line)}
.scale{position:relative;height:16px;margin:0 64px 6px 30px;font-size:11px;color:var(--mute);font-family:ui-monospace,Menlo,monospace}
.scale span{position:absolute;transform:translateX(-50%)}
.ch{display:grid;grid-template-columns:18px 1fr 52px;gap:12px;align-items:center;margin:8px 0}
.ch .lbl{color:var(--dim);font-size:12px;font-weight:700;text-align:right}
.bar.pro{height:22px;border-radius:6px;background:linear-gradient(90deg,transparent 0,transparent 90%,rgba(255,77,94,.10) 90%),#0c0e13}
.bar.pro i{background:linear-gradient(90deg,#1fd1ff 0%,var(--live) 60%,#e9ff5c 82%,#ffb340 92%,var(--off) 98%)}
.bar.pro b{width:3px;background:#fff}
.peak{font-size:13px;color:var(--fg);text-align:right;font-variant-numeric:tabular-nums}
.vufoot{display:flex;justify-content:space-between;align-items:center;margin-top:8px}
.clip{padding:5px 10px;border-radius:8px;font-size:11px;letter-spacing:.12em;background:#1a1d26;color:var(--mute);border:1px solid var(--line)}
.clip.on{background:var(--off);color:#fff;border-color:var(--off);animation:blink .6s steps(2) infinite}
@keyframes blink{50%{filter:brightness(.6)}}
.recipes{display:grid;gap:12px;margin-top:12px}
.recipe{background:#0c0f15;border:1px solid var(--line);border-radius:14px;padding:16px 18px;font-size:14px;color:var(--dim)}
.recipe h4{margin:0 0 6px;color:var(--fg);font-size:15px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.recipe .tag{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--live);font-weight:700}
.recipe p{margin:0 0 6px}.recipe ol{margin:6px 0 0;padding-left:20px}.recipe li{margin:4px 0}.recipe em{font-style:normal;color:var(--fg);font-weight:600}
.err{display:none;margin-top:14px;padding:12px 14px;border-radius:12px;background:rgba(255,77,94,.1);border:1px solid rgba(255,77,94,.35);color:#ffb3ba;font-size:14px}
details.other{margin-top:18px;border-top:1px solid var(--line);padding-top:14px}
details.other summary{cursor:pointer;color:var(--dim);font-weight:600;font-size:14px;list-style:none}
details.other summary::before{content:'▸ ';}details.other[open] summary::before{content:'▾ ';}
.liverow{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.livehead{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:800;letter-spacing:-.02em}.livehead .mono{color:var(--dim);font-size:18px;font-weight:600}
button.wide{flex:1;padding:18px;font-size:17px}
.meters{display:grid;gap:8px;margin-top:18px}
.meter{display:grid;grid-template-columns:18px 1fr;gap:10px;align-items:center;color:var(--dim);font-size:12px;font-weight:700}
.bar{height:16px;border-radius:8px;background:#0a0c10;border:1px solid var(--line);position:relative;overflow:hidden}
.bar i{position:absolute;inset:0;width:0;background:linear-gradient(90deg,#1fd1ff 0%,var(--live) 55%,#e9ff5c 82%,var(--off) 95%);transition:width .04s linear}
.bar b{position:absolute;top:0;bottom:0;width:2px;background:#fff;left:0;opacity:.85;transition:left .1s}
.status{display:flex;align-items:center;gap:12px;margin-top:18px;font-weight:600}
.status small{color:var(--dim);font-weight:500}
table{width:100%;border-collapse:collapse;margin-top:10px;font-size:15px}
td,th{text-align:left;padding:10px 6px;border-top:1px solid var(--line)}th{color:var(--dim);font-size:12px;letter-spacing:.1em;text-transform:uppercase;border:0}
td.r,th.r{text-align:right}td .dot{display:inline-block;margin-right:8px;vertical-align:middle}
.empty{color:var(--mute);padding:18px 0;text-align:center}
.hint{color:var(--dim);font-size:14px;margin-top:12px}.hint b{color:var(--fg)}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);opacity:0;background:#1a1f2b;border:1px solid var(--line);padding:12px 18px;border-radius:14px;transition:.25s;pointer-events:none;font-weight:600}
.toast.show{opacity:1;transform:translateX(-50%)}
/* projector view: URL + QR only, huge */
body.projector .card:not(.share),body.projector header .controls,body.projector .share .row,body.projector .share .hint{display:none}
body.projector .stname{font-size:28px}
body.projector .wrap.host{max-width:none;padding:40px 5vw}body.projector .share{padding:48px}
body.projector .sharegrid{grid-template-columns:1fr;gap:28px;text-align:center}
body.projector .sharegrid>div:first-child{order:2}body.projector .qr{order:1;justify-self:center}
body.projector .qr img,body.projector .qr canvas{width:min(48vh,560px);height:min(48vh,560px)}
body.projector .url{margin:6px 0 10px}body.projector .alt{font-size:20px}
/* station bar */
.stationbar{align-items:center;flex-wrap:wrap}.station{display:flex;align-items:center;gap:14px}
.stname{font-weight:900;letter-spacing:.16em;font-size:18px;cursor:text;text-transform:uppercase}.stname:hover{color:var(--live)}
.sttag{color:var(--dim);font-size:13px;letter-spacing:.06em}
.onair{padding:10px 18px;border-radius:12px;border:2px solid #3a1a1f;background:#1a0c0f;color:#6b2a33;font-weight:900;letter-spacing:.22em;font-size:14px;transition:.25s}
.onair.on{border-color:#ff2d55;background:#ff2d55;color:#fff;box-shadow:0 0 24px rgba(255,45,85,.7),0 0 60px rgba(255,45,85,.35);animation:glow 2.4s ease-in-out infinite}
.onair.muted{border-color:var(--wait);background:#2a1d08;color:var(--wait)}
@keyframes glow{50%{box-shadow:0 0 12px rgba(255,45,85,.5),0 0 30px rgba(255,45,85,.2)}}
.pill.count{font-size:15px}.pill.count b{color:var(--fg);font-size:20px;font-variant-numeric:tabular-nums}
.desk{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:12px}
@media(max-width:640px){.desk{grid-template-columns:1fr}}
.deskbtn{display:flex;flex-direction:column;align-items:flex-start;gap:2px;padding:18px 20px;border-radius:18px;background:#191d27;border:1px solid var(--line);text-align:left;min-height:96px;position:relative}
.deskbtn .k{position:absolute;right:16px;top:14px;font-size:18px;opacity:.5}.deskbtn span:not(.k){font-size:20px;font-weight:800}.deskbtn small{color:var(--dim);font-weight:500;font-size:13px}
.deskbtn.talk.on{background:#123a2a;border-color:var(--live);box-shadow:inset 0 0 0 2px var(--live)}.deskbtn.talk.on small{color:var(--live)}
.deskbtn.mute.on{background:#3a2a10;border-color:var(--wait);box-shadow:inset 0 0 0 2px var(--wait)}.deskbtn.mute.on small{color:var(--wait)}
.deskbtn.stop{background:#2a1418;border-color:#4a1f26}.deskbtn.stop:hover{background:var(--off);color:#fff}
.programme{margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}.programme label{display:block;margin-bottom:8px}
.programme input{flex:1}
/* receiver */
.radio{background:#0a0c10;border:1px solid var(--line);border-radius:16px;padding:16px 18px;margin-bottom:18px;background-image:repeating-linear-gradient(0deg,transparent 0 3px,rgba(255,255,255,.012) 3px 4px)}
.rtop{display:flex;align-items:center;gap:10px;font-weight:800;font-size:18px}.rtop .grow{flex:1}.rfreq{color:var(--mute);font-size:13px;font-weight:600}
.rnow{margin-top:10px;font-size:22px;font-weight:700;letter-spacing:-.01em;color:var(--live);min-height:30px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* listener */
.btns{display:grid;grid-template-columns:1fr 84px;gap:10px}
.big.lmute{background:#222736;color:var(--fg);font-size:24px;padding:0}.big.lmute.on{background:var(--wait);color:#3a2a10}
.listen{max-width:560px;margin:0 auto;padding:28px 20px 60px}
.big{width:100%;font-size:26px;padding:26px;border-radius:22px;background:var(--live);color:var(--ink);display:flex;align-items:center;justify-content:center;gap:14px}
.big.on{background:#222736;color:var(--fg)}
.big svg{width:28px;height:28px}
.state{margin:22px 0 4px;font-size:22px;font-weight:800;letter-spacing:-.02em;display:flex;align-items:center;gap:12px}
.sub{color:var(--dim);font-size:15px;min-height:22px}
input[type=range]{width:100%;accent-color:var(--live);margin:10px 0 0}
input[type=text]{font:inherit;width:100%;padding:12px 14px;border-radius:12px;background:#0b0d12;color:var(--fg);border:1px solid var(--line)}
label{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);font-weight:700}
.stats{display:flex;gap:18px;color:var(--mute);font-size:13px;margin-top:14px}
.seg{display:flex;gap:6px;background:#0b0d12;border:1px solid var(--line);border-radius:14px;padding:5px;margin-top:8px}
.seg button{flex:1;background:transparent;color:var(--dim);padding:11px;border-radius:10px}.seg button.on{background:#222736;color:var(--fg)}
.unmute{position:fixed;inset:0;background:rgba(7,8,11,.85);display:none;place-items:center;z-index:9}
.unmute.show{display:grid}
"""

# Shared client-side signalling core. Everything is serialised so messages
# can't overtake one another, and the poll loop notices server restarts.
JS_COMMON = r"""
const $=s=>document.querySelector(s);const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const rnd=()=>Math.random().toString(36).slice(2,10);
class Signal{
  constructor(id,extra){this.id=id;this.extra=extra||'';this.q=Promise.resolve();this.after=0;this.gen=null;this.online=null;this.dead=false;
    this.onmsg=async()=>{};this.onreset=()=>{};this.onoffline=()=>{};this.onstale=()=>{};}
  send(to,data){this.q=this.q.then(()=>this._post({from:this.id,to,data})).catch(()=>{});return this.q;}
  async _post(body){for(let i=0;i<4;i++){try{const r=await fetch('/api/msg',{method:'POST',body:JSON.stringify(body)});if(r.ok)return;}catch(e){}await sleep(250*(i+1));}}
  async run(){for(;;){if(this.dead)return;try{
    const ctl=new AbortController();const t=setTimeout(()=>ctl.abort(),40000);
    const r=await fetch(`/api/poll?id=${this.id}&after=${this.after}${this.extra}`,{signal:ctl.signal,cache:'no-store'});clearTimeout(t);
    if(!r.ok)throw new Error(r.status);const {gen,msgs,stale}=await r.json();
    if(stale){this.dead=true;this.onstale();return;}
    const restarted=this.gen!==null&&gen!==this.gen;const backOnline=this.online===false;
    this.gen=gen;this.online=true;if(restarted)this.after=0;
    if(restarted||backOnline)this.onreset();
    for(const m of msgs){this.after=Math.max(this.after,m.seq);try{await this.onmsg(m.from,m.data);}catch(e){console.warn('msg',e);}}
  }catch(e){if(this.online!==false){this.online=false;this.onoffline();}await sleep(1500);}}}
}
// Opus tuned for music: full-band stereo, 320 kb/s, no DTX, in-band FEC for the odd lost packet.
const OPUS='stereo=1;sprop-stereo=1;maxaveragebitrate=320000;maxplaybackrate=48000;sprop-maxcapturerate=48000;cbr=0;usedtx=0;useinbandfec=1;ptime=10;minptime=10';
function stereo(sdp){const m=sdp.match(/a=rtpmap:(\d+) opus\/48000/i);if(!m)return sdp;const pt=m[1];
  const re=new RegExp('^a=fmtp:'+pt+' (.*)$','gm');                 // every m-section (one per channel)
  if(re.test(sdp))return sdp.replace(re,(l,params)=>'a=fmtp:'+pt+' '+params.split(';').filter(kv=>!/^(stereo|sprop-stereo|maxaveragebitrate|maxplaybackrate|sprop-maxcapturerate|cbr|usedtx|useinbandfec|ptime|minptime)=/.test(kv.trim())).concat(OPUS.split(';')).join(';'));
  return sdp.replace(new RegExp('(a=rtpmap:'+pt+' opus\\/48000[^\\r\\n]*\\r?\\n)'),'$1a=fmtp:'+pt+' '+OPUS+'\r\n');}
// Stereo meter with peak hold. Returns a stop() function.
function meter(stream,els){
  const AC=window.AudioContext||window.webkitAudioContext;const ac=new AC();const src=ac.createMediaStreamSource(stream);
  const split=ac.createChannelSplitter(2);src.connect(split);
  const an=[0,1].map(i=>{const a=ac.createAnalyser();a.fftSize=1024;split.connect(a,i);return a;});
  const buf=new Float32Array(1024);const peak=[0,0];let alive=true;
  (function tick(){if(!alive)return;an.forEach((a,i)=>{a.getFloatTimeDomainData(buf);let s=0,p=0;for(const v of buf){s+=v*v;const av=Math.abs(v);if(av>p)p=av;}
    const db=20*Math.log10(Math.sqrt(s/buf.length)+1e-9);const pct=Math.max(0,Math.min(100,(db+60)/60*100));
    peak[i]=Math.max(pct,peak[i]-0.6);els[i].querySelector('i').style.width=pct+'%';els[i].querySelector('b').style.left=peak[i]+'%';});
    requestAnimationFrame(tick);})();
  if(ac.state==='suspended')ac.resume().catch(()=>{});
  return()=>{alive=false;els.forEach(e=>{e.querySelector('i').style.width=0;e.querySelector('b').style.left=0;});ac.close().catch(()=>{});};
}
function toast(t){const el=$('#toast');el.textContent=t;el.classList.add('show');clearTimeout(el._t);el._t=setTimeout(()=>el.classList.remove('show'),2200);}
const ICON_PLAY='<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const ICON_STOP='<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>';
const ICON_WAVE='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M4 12h1M8 8v8M12 4v16M16 8v8M20 12h1"/></svg>';
"""

HOST_CSS = r"""
:root{--paper:#f7f6f2;--card:#fbfaf8;--ink:#3b2d6e;--ink2:#8e7fc4;--ink3:#c9c1e3;--grid:rgba(59,45,110,.13);--live:#2f9e6d;--warn:#b9791d;--off:#c0392b;--onair:#d7263d}
*{box-sizing:border-box}html{color-scheme:light}
body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,Roboto,sans-serif;min-height:100vh;-webkit-font-smoothing:antialiased;position:relative;overflow-x:hidden}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
svg.bg{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.5}
.wrap{position:relative;z-index:1;max-width:980px;margin:0 auto;padding:22px 20px 60px}
header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:4px 4px 18px;flex-wrap:wrap}
.station{display:flex;align-items:center;gap:14px}
.logo{width:34px;height:34px;border-radius:9px;border:1.5px solid var(--ink);display:grid;place-items:center;color:var(--ink);background:var(--card)}
.logo svg{width:18px;height:18px}
.stname{font-weight:800;letter-spacing:.16em;font-size:19px;cursor:text;text-transform:uppercase}.stname:hover{color:var(--ink2)}
.sttag{color:var(--ink2);font-size:11px;letter-spacing:.16em;text-transform:uppercase}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.row>.grow{flex:1;min-width:160px}
.onair{padding:10px 18px;border-radius:10px;border:1.5px solid var(--ink3);color:var(--ink3);font-weight:900;letter-spacing:.22em;font-size:13px;transition:.25s;background:var(--card)}
.onair span{display:block}
.onair.on{border-color:var(--onair);background:var(--onair);color:#fff;box-shadow:0 0 0 4px rgba(215,38,61,.15),0 0 26px rgba(215,38,61,.35);animation:glow 2.4s ease-in-out infinite}
.onair.muted{border-color:var(--warn);background:var(--warn);color:#fff}
@keyframes glow{50%{box-shadow:0 0 0 4px rgba(215,38,61,.08),0 0 10px rgba(215,38,61,.2)}}
.pill{display:inline-flex;align-items:center;gap:8px;padding:8px 14px;border-radius:999px;border:1.5px solid var(--ink);color:var(--ink2);font-size:11px;letter-spacing:.14em;text-transform:uppercase;white-space:nowrap;background:var(--card)}
.pill.count b{color:var(--ink);font-size:18px;font-variant-numeric:tabular-nums;letter-spacing:0}
button{font:inherit;cursor:pointer;border:1.5px solid var(--ink);background:transparent;color:var(--ink);border-radius:12px;padding:12px 16px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;font-size:12px;transition:.15s;display:inline-flex;align-items:center;justify-content:center;gap:10px}
button:hover{background:rgba(59,45,110,.06)}button:active{transform:translateY(1px)}button:disabled{opacity:.35;cursor:default}
button.ghost{color:var(--ink2)}button.icon{padding:11px 14px}
.card{background:var(--card);border:1.5px solid var(--ink);border-radius:18px;padding:22px 24px;margin-bottom:18px;box-shadow:6px 6px 0 -1px var(--paper),6px 6px 0 0 var(--ink3)}
label,.lbl{display:block;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);margin-bottom:6px}
select,input[type=text]{font:inherit;width:100%;padding:12px 14px;border-radius:10px;background:var(--paper);color:var(--ink);border:1.5px solid var(--ink);appearance:none}
select{background-image:linear-gradient(45deg,transparent 50%,var(--ink) 50%),linear-gradient(135deg,var(--ink) 50%,transparent 50%);background-position:calc(100% - 20px) 50%,calc(100% - 14px) 50%;background-size:6px 6px;background-repeat:no-repeat}
.hint{color:var(--ink2);font-size:13px;margin-top:10px}.hint b{color:var(--ink)}
/* address */
.sharegrid{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:28px;align-items:center}.sharegrid>div:first-child{min-width:0}
@media(max-width:760px){.sharegrid{grid-template-columns:1fr}}
.url{font-size:48px;font-weight:800;letter-spacing:-.02em;line-height:1.1;white-space:nowrap;margin:4px 0 12px;color:var(--ink2)}.url b{color:var(--ink)}
.alt{color:var(--ink2);font-size:13px}.alt code{color:var(--ink)}
.qr{background:#fff;padding:12px;border-radius:14px;line-height:0;border:1.5px solid var(--ink);justify-self:end}
.qr img,.qr canvas{display:block;width:230px;height:230px}
/* channels */
.chhead{display:flex;align-items:center;gap:10px;margin-bottom:8px}.chhead label{flex:1}
.strip{display:grid;grid-template-columns:22px 150px minmax(0,1fr) 90px 40px;gap:10px;align-items:center;padding:6px 0;border-top:1.5px dashed var(--ink3)}
.strip:first-child{border-top:0}.strip .num{color:var(--ink2);font-size:12px;text-align:center}
.strip input.name{padding:9px 10px;font-weight:700;letter-spacing:.04em}.strip select{padding:9px 32px 9px 10px;font-size:13px}
.strip .mini{height:8px;border:1.5px solid var(--ink);border-radius:4px;overflow:hidden;background:var(--paper)}.strip .mini i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--ink2),var(--ink));transition:width .05s}
.strip .x{padding:7px 10px;font-size:14px}
@media(max-width:640px){.strip{grid-template-columns:22px 1fr 40px}.strip select{grid-column:2/3}.strip .mini{grid-column:2/3}}
/* clock */
.clockrow{display:grid;grid-template-columns:minmax(0,1.2fr) 110px minmax(0,1fr);gap:12px;align-items:end;margin-top:14px;padding-top:14px;border-top:1.5px dashed var(--ink3)}
@media(max-width:640px){.clockrow{grid-template-columns:1fr 1fr}.clockstat{grid-column:1/-1}}
.clockstat #clockst{font-size:14px;padding:11px 0 4px;color:var(--ink)}.clockstat #clockst.run{color:var(--live)}
.beats{display:flex;gap:6px;height:10px}.beats i{width:10px;height:10px;border-radius:50%;border:1.5px solid var(--ink);background:transparent}.beats i.on{background:var(--ink)}.beats i.one{border-color:var(--live)}.beats i.one.on{background:var(--live)}
/* input + metering */
.srcrow{display:flex;gap:10px;align-items:flex-end}.srcrow .grow{flex:1;min-width:0}
.devinfo{color:var(--ink2);font-size:13px;margin-top:10px;min-height:20px}.devinfo b{color:var(--ink)}
.vu{margin:18px 0 22px;padding:14px 16px 12px;border-radius:14px;background:var(--paper);border:1.5px solid var(--ink)}
.bezel{position:relative;border:1.5px solid var(--ink);border-radius:8px;overflow:hidden;background:linear-gradient(180deg,#fdfcfa,#f4f2ee);margin-bottom:14px}
canvas#scope{display:block;width:100%;height:150px}
.scale{position:relative;height:14px;margin:0 64px 6px 30px;font-size:10px;color:var(--ink2);font-family:ui-monospace,Menlo,monospace}
.scale span{position:absolute;transform:translateX(-50%)}
.ch{display:grid;grid-template-columns:18px 1fr 52px;gap:12px;align-items:center;margin:6px 0}
.ch .lbl{margin:0;text-align:right;font-weight:700;color:var(--ink2)}
.bar{height:14px;border:1.5px solid var(--ink);border-radius:7px;position:relative;overflow:hidden;background:repeating-linear-gradient(90deg,transparent 0 9.6%,var(--grid) 9.6% 10%),linear-gradient(90deg,transparent 90%,rgba(192,57,43,.10) 90%)}
.bar i{position:absolute;inset:0;width:0;background:linear-gradient(90deg,var(--ink2),var(--ink) 80%,var(--warn) 92%,var(--off) 98%);transition:width .04s linear}
.bar b{position:absolute;top:0;bottom:0;width:2px;background:var(--ink);left:0;transition:left .1s}
.peak{font-size:12px;text-align:right;font-variant-numeric:tabular-nums}
.vufoot{display:flex;justify-content:space-between;align-items:center;margin-top:8px;gap:12px}
.clip{padding:4px 10px;border-radius:7px;font-size:10px;letter-spacing:.14em;border:1.5px solid var(--ink3);color:var(--ink3)}
.clip.on{background:var(--off);color:#fff;border-color:var(--off);animation:blink .6s steps(2) infinite}
@keyframes blink{50%{filter:brightness(.7)}}
/* go live + desk */
.hero{width:100%;padding:20px 24px;border-radius:14px;background:var(--ink);color:#fff;border-color:var(--ink);text-transform:none;letter-spacing:0;justify-content:center;gap:16px}
.hero:hover{background:#2e2257}.hero:disabled{background:transparent;color:var(--ink);opacity:.4}
.hero svg{width:28px;height:28px;flex:none}.hero span{display:flex;flex-direction:column;line-height:1.15;text-align:left}
.hero b{font-size:24px;letter-spacing:.06em;text-transform:uppercase}.hero small{font-size:13px;font-weight:500;opacity:.8}
.desk{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:12px}
@media(max-width:640px){.desk{grid-template-columns:1fr}}
.deskbtn{flex-direction:column;align-items:flex-start;gap:2px;padding:16px 18px;border-radius:14px;text-align:left;min-height:92px;position:relative;text-transform:none;letter-spacing:0}
.deskbtn .k{position:absolute;right:14px;top:12px;font-size:16px;opacity:.55}.deskbtn span:not(.k){font-size:18px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.deskbtn small{color:var(--ink2);font-weight:500;font-size:12px}
.deskbtn.talk.on{background:var(--live);border-color:var(--live);color:#fff}.deskbtn.talk.on small{color:#e8fff2}
.deskbtn.mute.on{background:var(--warn);border-color:var(--warn);color:#fff}.deskbtn.mute.on small{color:#fff3df}
.deskbtn.stop{border-color:var(--off);color:var(--off)}.deskbtn.stop small{color:var(--off);opacity:.8}.deskbtn.stop:hover{background:var(--off);color:#fff}.deskbtn.stop:hover small{color:#fff}
.err{display:none;margin-top:14px;padding:12px 14px;border-radius:10px;border:1.5px solid var(--off);color:var(--off);font-size:13px;background:rgba(192,57,43,.05)}
.programme{margin-top:22px;padding-top:18px;border-top:1.5px dashed var(--ink3)}.programme input{flex:1}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);opacity:0;background:var(--ink);color:#fff;padding:12px 18px;border-radius:12px;transition:.25s;pointer-events:none;font-weight:600;z-index:9}
.toast.show{opacity:1;transform:translateX(-50%)}
/* projector view: URL + QR only, huge */
body.projector .card:not(.share),body.projector header .controls,body.projector .share .row,body.projector .share .hint{display:none}
body.projector .wrap{max-width:none;padding:40px 5vw}body.projector .share{padding:48px}
body.projector .sharegrid{grid-template-columns:1fr;gap:28px;text-align:center}
body.projector .sharegrid>div:first-child{order:2}body.projector .qr{order:1;justify-self:center}
body.projector .qr img,body.projector .qr canvas{width:min(48vh,560px);height:min(48vh,560px)}
body.projector .url{margin:6px 0 10px}body.projector .alt{font-size:20px}body.projector .stname{font-size:28px}
"""

HOST_HTML = r"""<!doctype html><html><head><meta charset=utf-8><title>CCAST · Studio</title>
<meta name=viewport content="width=device-width,initial-scale=1"><style>%HOST_CSS%</style></head><body>
%BG%
<div class="wrap host">
<header class=stationbar>
 <div class=station>
  <span class=logo>%ICON%</span>
  <div><div class=stname id=stname title="Click to rename">CCAST</div><div class=sttag id=sttag>studio</div></div>
 </div>
 <div class="row controls">
  <div class=onair id=onair><span>OFF AIR</span></div>
  <span class="pill count"><b id=n>0</b> listening</span>
  <button class="ghost icon" id=proj title="Only the address and QR, huge — for the projector">⛶ Projector</button>
  <button class="ghost icon" id=quit title="Stop streaming and quit CCAST">Quit</button>
 </div>
</header>

<section class="card share">
 <div class=sharegrid>
  <div>
   <div class="url mono" id=url>…</div>
   <div class=alt id=alts></div>
   <div class=row style="margin-top:16px"><button class=ghost id=copy>Copy link</button></div>
   <div class=hint>Same Wi-Fi as this Mac · any browser or phone · press <b>Listen</b> · leave the tab open while working.</div>
  </div>
  <div class=qr id=qr></div>
 </div>
</section>

<section class=card id=studio>
 <div class=chhead><label style="margin:0">Channels · each is the first stereo pair of a device</label><button class="ghost icon" id=addch>+ Channel</button><button class="ghost icon" id=rescan title="Rescan audio devices">⟳</button></div>
 <div id=strips></div>
 <div class=clockrow>
  <div><label>Clock · MIDI in</label><select id=midiin><option value="">— connect —</option></select></div>
  <div><label>Beats / bar</label><select id=bpb><option>2</option><option>3</option><option selected>4</option><option>5</option><option>6</option><option>7</option></select></div>
  <div class=clockstat><label>DAW transport</label><div class="mono" id=clockst>no clock</div><div class=beats id=beats></div></div>
 </div>
 <div class=hint id=clockhint>Send MIDI Clock from the DAW to the <b>IAC Driver</b> (Audio MIDI Setup → MIDI Studio → IAC Driver → <i>Device is online</i>). Ableton Live: Preferences → Link/Tempo/MIDI → Output IAC Bus → <b>Sync</b> on. Pro Tools: Setup → Peripherals → Synchronization → <b>MIDI Beat Clock</b> → IAC Bus. Students then get a native click that follows your transport.</div>
 <div class=hint id=chhint>One channel = the normal stream. Add more and every student gets their own cue mix: knobs, mute and solo per channel. Six virtual stereo devices come with this Mac (Pro Tools Audio Bridge 2‑A, 2‑B, 6, 16, 32, 64) — combine them with your interface in an Aggregate Device and route DAW sends to them.</div>

 <div class=vu>
  <div class=bezel><canvas id=scope></canvas></div>
  <div class=scale id=scale></div>
  <div class=ch><span class=lbl>L</span><div class="bar pro" id=mL><i></i><b></b></div><span class="peak mono" id=pL>−∞</span></div>
  <div class=ch><span class=lbl>R</span><div class="bar pro" id=mR><i></i><b></b></div><span class="peak mono" id=pR>−∞</span></div>
  <div class=vufoot><span id=sig class=hint style="margin:0">No signal</span><span class=hint id=acwarn style="margin:0;color:var(--warn);display:none">Click anywhere to start the audio engine</span><button class="clip" id=clip title="Peak ≥ −0.1 dBFS — click to reset">CLIP</button></div>
 </div>

 <div id=idle>
  <button class=hero id=go disabled>%PLAY%<span><b>Go live</b><small id=gosub>Select an input first</small></span></button>
 </div>
 <div id=live hidden>
  <div class=desk>
   <button class="deskbtn talk" id=talk><span class=k>🎙</span><span>Talkback</span><small>hold to talk · click to latch · key T</small></button>
   <button class="deskbtn mute" id=mute><span class=k>M</span><span>Mute</span><small>programme off, students stay</small></button>
   <button class="deskbtn stop" id=stop><span class=k>■</span><span>Stop</span><small>go off air</small></button>
  </div>
  <div class="row" style="margin-top:12px"><div class=grow><label>Talkback mic</label><select id=mic></select></div><div class=hint id=src style="margin:0;flex:2;min-width:220px"></div></div>
 </div>
 <div class=err id=err></div>

 <div class=programme>
  <label>Now playing · shown on every receiver</label>
  <div class=row><input type=text id=now class=grow maxlength=90 placeholder="e.g. Sidechain compression demo · or a reference: Burial — Archangel"><button id=sendnow>Update</button><button class=ghost id=clearnow title="Clear">×</button></div>
 </div>
</section>
</div>
<div class=toast id=toast></div>
<script src="/qrcode.min.js"></script>
<script>%JS%
const peers=new Map();           // id -> {pc,cid,pending:[],senders:{stemId:RTCRtpSender}}
const HID=rnd();                 // this host page instance; students ignore host-ready from a host they're already connected to
const sig=new Signal('host','&hid='+HID);
let live=false,muted=false,talking=false,hasSignal=false,t0=0,micStream=null,micSrc=null,devices=[];
const meta={station:'CCAST',now:''};try{Object.assign(meta,JSON.parse(localStorage.cc_meta||'{}'));}catch(e){}
if(/classcast radio/i.test(meta.station))meta.station='CCAST';
// ---------- the desk: one Web Audio graph.
//   channel i:  device -> g_i (duck / mute) -> dest_i  => its own Opus track
//   talkback:   mic -> micGain -> tbDest                 => its own track, never ducked
//   everything also sums into `monitor` for the studio scope and meters (not sent).
const AC=window.AudioContext||window.webkitAudioContext;const ac=new AC({sampleRate:48000,latencyHint:'interactive'});
const monitor=ac.createGain();const dest=ac.createMediaStreamDestination();monitor.connect(dest);
const micGain=ac.createGain();micGain.gain.value=0;const tbDest=ac.createMediaStreamDestination();micGain.connect(tbDest);micGain.connect(monitor);
const tbTrack=tbDest.stream.getAudioTracks()[0];try{tbTrack.contentHint='speech';}catch(e){}
const MAXCH=6;let stems=[];                   // [{id,name,deviceId,stream,src,g,dest,track,an}]
try{const saved=JSON.parse(localStorage.cc_stems||'[]');saved.slice(0,MAXCH).forEach(s=>addStem(s.name,s.deviceId,false));}catch(e){}
if(!stems.length)addStem('Mix','',false);
function addStem(name,deviceId,persist=true){const g=ac.createGain(),d=ac.createMediaStreamDestination(),an=ac.createAnalyser();an.fftSize=512;g.connect(d);g.connect(monitor);g.connect(an);
  const track=d.stream.getAudioTracks()[0];try{track.contentHint='music';}catch(e){}
  const s={id:'c'+rnd(),name:name||('Ch '+(stems.length+1)),deviceId:deviceId||'',stream:null,src:null,g,dest:d,track,an};stems.push(s);if(persist)saveStems();return s;}
function removeStem(s){if(stems.length<=1)return;if(s.src)s.src.disconnect();if(s.stream)s.stream.getTracks().forEach(t=>t.stop());s.g.disconnect();stems=stems.filter(x=>x!==s);saveStems();renderStrips();if(live)renegotiateAll();updateGo();}
function saveStems(){try{localStorage.cc_stems=JSON.stringify(stems.map(s=>({name:s.name,deviceId:s.deviceId})));}catch(e){}}
function ramp(g,v,ms){const t=ac.currentTime;g.gain.cancelScheduledValues(t);g.gain.setValueAtTime(g.gain.value,t);g.gain.linearRampToValueAtTime(v,t+ms/1000);}
function applyGains(){for(const s of stems)ramp(s.g,muted?0:(talking?0.25:1),60);ramp(micGain,talking?1:0,40);}
function engine(){if(ac.state!=='running')ac.resume().catch(()=>{});$('#acwarn').style.display=ac.state==='running'?'none':'';}
['pointerdown','keydown'].forEach(ev=>addEventListener(ev,engine,{capture:true}));ac.onstatechange=engine;
// ---------- address / QR / station
function fitUrl(){const el=$('#url');const chars=(el.textContent||'').length||24;el.style.fontSize='10px';const w=el.clientWidth||600;el.style.fontSize=Math.max(22,Math.min(150,w/(chars*0.61)))+'px';}
addEventListener('resize',fitUrl);
fetch('/api/info').then(r=>r.json()).then(i=>{
  const u=new URL(i.url);$('#url').innerHTML=`<span style="color:var(--ink2)">http://</span><b>${u.hostname}</b><span style="color:var(--ink2)">:${u.port}</span>`;
  $('#url').dataset.url=i.url;fitUrl();
  if(window.QRCode)new QRCode($('#qr'),{text:i.url,width:480,height:480,correctLevel:QRCode.CorrectLevel.M});
  if(i.alts.length)$('#alts').innerHTML='Also: '+i.alts.map(a=>`<code>http://${a}:${i.port}</code>`).join(' · ');
  requestAnimationFrame(fitUrl);setTimeout(fitUrl,300);
});
if(window.ResizeObserver)new ResizeObserver(()=>fitUrl()).observe($('.sharegrid'));
$('#copy').onclick=async()=>{try{await navigator.clipboard.writeText($('#url').dataset.url);toast('Link copied');}catch(e){toast($('#url').dataset.url);}};
$('#proj').onclick=()=>{document.body.classList.toggle('projector');setTimeout(fitUrl,30);};
addEventListener('keydown',e=>{if(e.key==='Escape'){document.body.classList.remove('projector');setTimeout(fitUrl,30);}});
$('#quit').onclick=async()=>{if(!confirm('Quit CCAST? Students will be disconnected.'))return;stopAll();await fetch('/api/quit').catch(()=>{});
  document.body.innerHTML='<div class=wrap style="text-align:center;padding-top:20vh"><div class=station style="justify-content:center;margin-bottom:18px"><span class=logo>'+ICON_WAVE+'</span><span class=stname>CCAST</span></div><p style="color:var(--ink2)">CCAST has quit. You can close this tab.</p></div>';};
function saveMeta(){try{localStorage.cc_meta=JSON.stringify(meta);}catch(e){}}
function renderMeta(){$('#stname').textContent=meta.station;$('#now').value=meta.now;document.title=meta.station+' · Studio';}
function broadcastMeta(to){sig.send(to||'*',{type:'meta',station:meta.station,now:meta.now,live,muted,talking});}
$('#stname').onclick=()=>{const v=prompt('Name',meta.station);if(v&&v.trim()){meta.station=v.trim().slice(0,40);saveMeta();renderMeta();broadcastMeta();}};
$('#sendnow').onclick=()=>{meta.now=$('#now').value.trim();saveMeta();broadcastMeta();toast(meta.now?'Receivers updated':'Cleared');};
$('#now').onkeydown=e=>{if(e.key==='Enter')$('#sendnow').click();};$('#clearnow').onclick=()=>{$('#now').value='';$('#sendnow').click();};
renderMeta();
// ---------- channel strips
function renderStrips(){const box=$('#strips');box.innerHTML='';stems.forEach((s,i)=>{const el=document.createElement('div');el.className='strip';el.innerHTML=`
  <span class="num mono">${i+1}</span>
  <input type=text class=name value="${s.name.replace(/"/g,'&quot;')}" maxlength=14 title="Channel name, shown to students">
  <select class=dev></select>
  <div class="mini"><i></i></div>
  <button class="ghost icon x" title="Remove channel" ${stems.length<=1?'disabled':''}>×</button>`;
  const sel=el.querySelector('select');fillSelect(sel,s.deviceId,/bridge|loopback|blackhole|aggregate/i);
  el.querySelector('.name').onchange=e=>{s.name=e.target.value.trim()||('Ch '+(i+1));saveStems();if(live)announceStems();};
  sel.onchange=()=>{s.deviceId=sel.value;saveStems();arm(s);};
  el.querySelector('.x').onclick=()=>removeStem(s);
  s.miniEl=el.querySelector('.mini i');box.append(el);});
  $('#addch').disabled=stems.length>=MAXCH;$('#chhint').style.display=stems.length>1?'none':'';}
function fillSelect(sel,cur,prefer){sel.innerHTML='';for(const d of devices){const o=document.createElement('option');o.value=d.deviceId;o.textContent=d.label||('Input '+d.deviceId.slice(0,6));sel.append(o);}
  if(cur&&[...sel.options].some(o=>o.value===cur))sel.value=cur;else{const p=[...sel.options].find(o=>prefer.test(o.textContent));if(p)sel.value=p.value;}}
$('#addch').onclick=()=>{if(stems.length>=MAXCH)return;const s=addStem('Ch '+(stems.length+1),'');renderStrips();
  // pick the first device no other channel uses
  const used=new Set(stems.map(x=>x.deviceId));const free=devices.find(d=>!used.has(d.deviceId));if(free){s.deviceId=free.deviceId;saveStems();renderStrips();}arm(s);if(live)renegotiateAll();};
async function listDevices(){devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput');renderStrips();
  const cur=$('#mic').value||localStorage.cc_mic||'';fillSelect($('#mic'),cur,/macbook|built-in|microphone|mic/i);return devices.length;}
function describe(tr){const st=tr.getSettings();const ch=st.channelCount;const proc=(st.echoCancellation||st.noiseSuppression||st.autoGainControl);
  return `${tr.label} · ch 1–2 · ${ch===2?'stereo':ch===1?'<b style="color:var(--warn)">mono</b>':ch+' ch'}`+(st.sampleRate?` · ${(st.sampleRate/1000).toFixed(1).replace('.0','')} kHz`:'')+(proc?' · <b style="color:var(--off)">processing ON</b>':'');}
async function arm(s){if(!s.deviceId){return;}showErr('');
  try{const st=await navigator.mediaDevices.getUserMedia({audio:{deviceId:{exact:s.deviceId},channelCount:{ideal:2},sampleRate:{ideal:48000},echoCancellation:false,noiseSuppression:false,autoGainControl:false,latency:0.005}});
    const tr=st.getAudioTracks()[0];try{tr.contentHint='music';}catch(e){}
    tr.onended=()=>{if(s.stream===st){showErr(`Channel "${s.name}": the input device went away.`);disarm(s);}};
    const old=s.stream;s.stream=st;if(s.src)s.src.disconnect();s.src=ac.createMediaStreamSource(st);s.src.connect(s.g);
    if(old&&old!==st)old.getTracks().forEach(t=>t.stop());engine();updateGo();srcLine();}
  catch(e){showErr(`Channel "${s.name}": could not open that input — ${e.message}`);disarm(s);}}
function disarm(s){if(s.src)s.src.disconnect();s.src=null;if(s.stream)s.stream.getTracks().forEach(t=>t.stop());s.stream=null;updateGo();}
function armAll(){stems.forEach(s=>{if(!s.stream)arm(s);});}
async function openMic(){const id=$('#mic').value;if(!id)return false;
  try{const s=await navigator.mediaDevices.getUserMedia({audio:{deviceId:{exact:id},channelCount:{ideal:1},echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(micStream)micStream.getTracks().forEach(t=>t.stop());if(micSrc)micSrc.disconnect();micStream=s;micSrc=ac.createMediaStreamSource(s);micSrc.connect(micGain);try{localStorage.cc_mic=id;}catch(e){}return true;}
  catch(e){showErr('Could not open the talkback mic: '+e.message);return false;}}
function srcLine(){const armed=stems.filter(s=>s.stream);$('#src').innerHTML=armed.length===1?describe(armed[0].stream.getAudioTracks()[0])+' · Opus 320 kb/s':armed.length+' channels · Opus 320 kb/s each · 10 ms frames';}
function updateGo(){const ok=stems.some(s=>s.stream);$('#go').disabled=!ok;
  $('#gosub').textContent=!ok?'Select an input first':hasSignal?(stems.length>1?stems.length+' channels · stereo · Opus 320 kb/s':'Stereo · 48 kHz · Opus 320 kb/s'):'No signal yet — you can still go live';
  $('#sig').textContent=!ok?'No input':hasSignal?'Signal present':'Silence';$('#sig').style.color=hasSignal?'var(--live)':'var(--ink2)';}
$('#mic').onchange=()=>{if(micStream)openMic();};$('#rescan').onclick=async()=>{await listDevices();armAll();};
navigator.mediaDevices.ondevicechange=async()=>{await listDevices();armAll();};
(async()=>{try{(await navigator.mediaDevices.getUserMedia({audio:true})).getTracks().forEach(t=>t.stop());}catch(e){}   // permission once -> labels + real IPs in ICE
  if(await listDevices()){if(!stems[0].deviceId){const p=devices.find(d=>/bridge 2|loopback|blackhole|aggregate/i.test(d.label))||devices[0];if(p){stems[0].deviceId=p.deviceId;saveStems();renderStrips();}}armAll();}else showErr('No audio input devices found.');engine();})();
// ---------- status
function setOnAir(){$('#onair').classList.toggle('on',live&&!muted);$('#onair').classList.toggle('muted',live&&muted);
  $('#onair').querySelector('span').textContent=!live?'OFF AIR':muted?'MUTED':talking?'TALKBACK':'ON AIR';}
function showErr(t){$('#err').innerHTML=t;$('#err').style.display=t?'block':'none';}
setInterval(()=>{if(!live)return;const s=Math.floor((Date.now()-t0)/1000);$('#sttag').innerHTML='on air <span class=mono>'+String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')+'</span>';},500);
// ---------- metering of the monitor sum + scope + per-channel minis
(function buildScale(){const el=$('#scale');[-60,-48,-36,-24,-18,-12,-6,-3,0].forEach(db=>{const t=document.createElement('span');t.style.left=((db+60)/60*100)+'%';t.textContent=db===0?'0':db;el.append(t);});})();
(function proMeter(){
  const split=ac.createChannelSplitter(2);const tap=ac.createGain();tap.channelCount=2;tap.channelCountMode='explicit';monitor.connect(tap);tap.connect(split);
  const an=[0,1].map(i=>{const a=ac.createAnalyser();a.fftSize=2048;split.connect(a,i);return a;});
  const bufs=[new Float32Array(2048),new Float32Array(2048)],mini=new Float32Array(512);const bars=[$('#mL'),$('#mR')],nums=[$('#pL'),$('#pR')];const hold=[-99,-99],holdT=[0,0];let quiet=60;
  const pct=db=>Math.max(0,Math.min(100,(db+60)/60*100));
  const cv=$('#scope'),cx=cv.getContext('2d');
  function scope(){const d=devicePixelRatio||1,r=cv.getBoundingClientRect();if(cv.width!==Math.round(r.width*d))cv.width=Math.round(r.width*d);if(cv.height!==Math.round(r.height*d))cv.height=Math.round(r.height*d);
    const W=cv.width,H=cv.height;cx.clearRect(0,0,W,H);cx.lineWidth=d;cx.strokeStyle='rgba(59,45,110,.14)';cx.setLineDash([3*d,5*d]);
    for(let i=1;i<10;i++){const x=W*i/10;cx.beginPath();cx.moveTo(x,0);cx.lineTo(x,H);cx.stroke();}for(let i=1;i<4;i++){const y=H*i/4;cx.beginPath();cx.moveTo(0,y);cx.lineTo(W,y);cx.stroke();}
    cx.setLineDash([]);cx.strokeStyle='rgba(59,45,110,.3)';cx.beginPath();cx.moveTo(0,H/2);cx.lineTo(W,H/2);cx.stroke();
    if(ac.state!=='running'||!stems.some(s=>s.stream))return;const L=bufs[0],R=bufs[1];let start=0;for(let k=1;k<680;k++){if(L[k-1]<0&&L[k]>=0){start=k;break;}}
    const span=1200,amp=H*0.44;const trace=(b,c,w)=>{cx.strokeStyle=c;cx.lineWidth=w*d;cx.beginPath();for(let k=0;k<span;k++){const x=W*k/span,y=H/2-b[start+k]*amp;k?cx.lineTo(x,y):cx.moveTo(x,y);}cx.stroke();};
    trace(R,'rgba(142,127,196,.9)',1.1);trace(L,'#3b2d6e',1.5);}
  (function tick(){const now=performance.now();let any=false;
    an.forEach((a,i)=>{a.getFloatTimeDomainData(bufs[i]);const buf=bufs[i];let sum=0,pk=0;for(let k=0;k<buf.length;k++){const v=buf[k],av=v<0?-v:v;sum+=v*v;if(av>pk)pk=av;}
      const rms=20*Math.log10(Math.sqrt(sum/buf.length)+1e-9),peak=20*Math.log10(pk+1e-9);
      if(peak>=hold[i]||now-holdT[i]>1500){hold[i]=peak;holdT[i]=now;}
      bars[i].querySelector('i').style.width=pct(rms)+'%';bars[i].querySelector('b').style.left=pct(hold[i])+'%';
      nums[i].textContent=hold[i]<-90?'−∞':(hold[i]>=0?'+':'')+hold[i].toFixed(1).replace('-','−');
      if(peak>=-0.1)$('#clip').classList.add('on');if(peak>-60)any=true;});
    for(const s of stems){if(!s.miniEl)continue;s.an.getFloatTimeDomainData(mini);let sm=0;for(let k=0;k<512;k++)sm+=mini[k]*mini[k];s.miniEl.style.width=pct(20*Math.log10(Math.sqrt(sm/512)+1e-9))+'%';}
    quiet=any?0:quiet+1;const sigNow=quiet<45;if(sigNow!==hasSignal){hasSignal=sigNow;updateGo();}
    scope();requestAnimationFrame(tick);})();
})();
$('#clip').onclick=()=>$('#clip').classList.remove('on');
// ---------- clock: MIDI Clock in (Web MIDI), fitted to a tempo/phase line, sent to students over data channels
const clock={running:false,bpm:0,bpb:4,ticks:0,line:null,seq:0,lastTick:0};let midi=null;const tickT=[];
try{clock.bpb=parseInt(localStorage.cc_bpb)||4;}catch(e){}$('#bpb').value=clock.bpb;
$('#bpb').onchange=()=>{clock.bpb=parseInt($('#bpb').value)||4;try{localStorage.cc_bpb=clock.bpb;}catch(e){}renderBeats();sendClockAll();};
function fitLine(){if(tickT.length<12)return;const n=tickT.length;let sk=0,st=0,skk=0,skt=0;for(const [k,t] of tickT){sk+=k;st+=t;skk+=k*k;skt+=k*t;}
  const b=(n*skt-sk*st)/(n*skk-sk*sk),a=(st-b*sk)/n;if(!(b>0))return;const [kl]=tickT[n-1];
  clock.bpm=60000/(b*24);clock.line={beat:kl/24,t:a+b*kl,bpm:clock.bpm};}
function onMidi(e){const st=e.data[0],t=e.timeStamp;
  if(st===0xF8){clock.ticks++;clock.lastTick=t;tickT.push([clock.ticks,t]);if(tickT.length>96)tickT.shift();if(!clock.running){clock.running=true;sendClockAll();}fitLine();if(clock.ticks%24===0)sendClockAll();renderClock();}
  else if(st===0xFA){clock.ticks=0;tickT.length=0;clock.running=true;clock.line=null;sendClockAll();renderClock();}
  else if(st===0xFB){clock.running=true;sendClockAll();renderClock();}
  else if(st===0xFC){clock.running=false;sendClockAll();renderClock();}
  else if(st===0xF2){clock.ticks=((e.data[1]|0)|((e.data[2]|0)<<7))*6;tickT.length=0;clock.line=null;}}
setInterval(()=>{if(clock.running&&performance.now()-clock.lastTick>600){clock.running=false;sendClockAll();renderClock();}},250);   // ticks stopped without a Stop message
function renderClock(){const el=$('#clockst');if(!midi){el.textContent='no clock';el.classList.remove('run');return;}
  const bpm=clock.bpm?clock.bpm.toFixed(1)+' bpm':'— bpm';const beat=Math.floor(clock.ticks/24);const bar=Math.floor(beat/clock.bpb)+1,bib=beat%clock.bpb+1;
  el.textContent=(clock.running?'▶ ':'■ ')+bpm+(clock.ticks?`  ·  ${bar}.${bib}`:'')+(clock.running?'':'  stopped');el.classList.toggle('run',clock.running);renderBeats(bib-1);}
function renderBeats(active){const b=$('#beats');if(b.children.length!==clock.bpb){b.innerHTML='';for(let i=0;i<clock.bpb;i++){const d=document.createElement('i');if(i===0)d.className='one';b.append(d);}}
  [...b.children].forEach((d,i)=>d.classList.toggle('on',clock.running&&i===active));}
function clockMsg(){clock.seq++;const l=clock.line;return JSON.stringify({type:'clock',seq:clock.seq,running:clock.running,bpm:clock.bpm,bpb:clock.bpb,beat:l?l.beat:clock.ticks/24,t:l?l.t:performance.now(),now:performance.now()});}
function sendClockAll(){const m=clockMsg();for(const p of peers.values())if(p.dc&&p.dc.readyState==='open'){try{p.dc.send(m);}catch(e){}}}
async function midiSetup(){if(!navigator.requestMIDIAccess){$('#midiin').innerHTML='<option value="">Web MIDI not available</option>';return;}
  try{midi=await navigator.requestMIDIAccess({sysex:false});}catch(e){$('#midiin').innerHTML='<option value="">MIDI access denied</option>';return;}
  const fill=()=>{const cur=$('#midiin').value||localStorage.cc_midi||'';$('#midiin').innerHTML='<option value="">off</option>';for(const inp of midi.inputs.values()){const o=document.createElement('option');o.value=inp.id;o.textContent=inp.name;$('#midiin').append(o);}
    if(cur&&[...$('#midiin').options].some(o=>o.value===cur))$('#midiin').value=cur;else{const iac=[...midi.inputs.values()].find(i=>/iac/i.test(i.name));if(iac)$('#midiin').value=iac.id;}attach();};
  const attach=()=>{for(const inp of midi.inputs.values())inp.onmidimessage=null;const inp=midi.inputs.get($('#midiin').value);if(inp){inp.onmidimessage=onMidi;try{localStorage.cc_midi=inp.id;}catch(e){}}
    clock.running=false;tickT.length=0;clock.line=null;renderClock();};
  midi.onstatechange=fill;$('#midiin').onchange=attach;fill();}
midiSetup();
// ---------- peers: one track per channel + talkback, all in one connection
function render(){$('#n').textContent=[...peers.values()].filter(p=>p.pc.connectionState==='connected').length;}
function dropPeer(id){const p=peers.get(id);if(p){p.pc.close();peers.delete(id);render();}}
function tuneSender(sn,kbps){try{const prm=sn.getParameters();prm.encodings=prm.encodings&&prm.encodings.length?prm.encodings:[{}];prm.encodings[0].maxBitrate=kbps*1000;prm.encodings[0].priority='high';prm.encodings[0].networkPriority='high';sn.setParameters(prm).catch(()=>{});}catch(e){}}
function stemList(pc,p){const byTrack=new Map();for(const s of stems)if(s.stream)byTrack.set(s.track,s);byTrack.set(tbTrack,{id:'tb',name:'Talkback'});
  return pc.getTransceivers().map(t=>{const s=byTrack.get(t.sender.track);return s?{mid:t.mid,id:s.id,name:s.name,kind:s.id==='tb'?'tb':'ch'}:null;}).filter(Boolean);}
function makePeer(id){const old=peers.get(id);if(old)old.pc.close();
  const pc=new RTCPeerConnection({iceServers:[]});const p={pc,cid:rnd(),pending:[],senders:{}};peers.set(id,p);
  for(const s of stems){if(!s.stream)continue;const sn=pc.addTrack(s.track,s.dest.stream);p.senders[s.id]=sn;tuneSender(sn,320);}
  const tsn=pc.addTrack(tbTrack,tbDest.stream);p.senders.tb=tsn;tuneSender(tsn,96);
  // data channel: clock timeline + clock-offset pings (unordered, no retransmits: newest wins)
  const dc=pc.createDataChannel('clock',{ordered:false,maxRetransmits:0});p.dc=dc;
  dc.onopen=()=>{try{dc.send(clockMsg());}catch(e){}};
  dc.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type==='ping')dc.send(JSON.stringify({type:'pong',t1:m.t1,t2:performance.now()}));else if(m.type==='clock?')dc.send(clockMsg());}catch(err){}};
  pc.onicecandidate=e=>{if(e.candidate)sig.send(id,{type:'ice',cid:p.cid,c:e.candidate});};
  pc.onconnectionstatechange=()=>{render();const s=pc.connectionState;
    if(s==='failed'||s==='closed'){if(peers.get(id)===p)dropPeer(id);}
    if(s==='disconnected')setTimeout(()=>{if(peers.get(id)===p&&pc.connectionState==='disconnected')dropPeer(id);},15000);};
  pc.createOffer().then(o=>{o.sdp=stereo(o.sdp);return pc.setLocalDescription(o);})
    .then(()=>{sig.send(id,{type:'offer',cid:p.cid,hid:HID,sdp:pc.localDescription,stems:stemList(pc,p)});broadcastMeta(id);})
    .catch(e=>console.warn(e));render();}
function renegotiateAll(){for(const id of [...peers.keys()])makePeer(id);}
function announceStems(){for(const [id,p] of peers)sig.send(id,{type:'stems',stems:stemList(p.pc,p)});}
sig.onmsg=async(from,d)=>{const p=peers.get(from);
  if(d.type==='hello'){if(live)makePeer(from);else{sig.send(from,{type:'wait'});broadcastMeta(from);}}
  else if(d.type==='answer'){if(p&&p.cid===d.cid&&p.pc.signalingState==='have-local-offer'){await p.pc.setRemoteDescription(d.sdp);
    for(const c of p.pending)await p.pc.addIceCandidate(c).catch(()=>{});p.pending=[];}}
  else if(d.type==='ice'){if(p&&p.cid===d.cid){if(p.pc.remoteDescription)await p.pc.addIceCandidate(d.c).catch(()=>{});else p.pending.push(d.c);}}
  else if(d.type==='active'){if(!p)return;for(const [sid,on] of Object.entries(d.active||{})){const sn=p.senders[sid];if(!sn)continue;      // a student's own cue: switch off channels they muted
    try{const prm=sn.getParameters();if(prm.encodings&&prm.encodings.length){prm.encodings[0].active=!!on;sn.setParameters(prm).catch(()=>{});}}catch(e){}}}
  else if(d.type==='bye'){dropPeer(from);}};
sig.onreset=()=>{sig.send('*',{type:'host-ready',hid:HID});broadcastMeta();};
sig.onoffline=()=>{toast('Server not reachable — click the CCAST app to start it again');};
// ---------- desk actions
$('#go').onclick=()=>{if(!stems.some(s=>s.stream))return;engine();live=true;muted=false;talking=false;t0=Date.now();applyGains();srcLine();
  $('#idle').hidden=true;$('#live').hidden=false;setOnAir();$('#mute').classList.remove('on');$('#talk').classList.remove('on');
  renegotiateAll();sig.send('*',{type:'host-ready',hid:HID});broadcastMeta();};
$('#mute').onclick=()=>{if(!live)return;muted=!muted;applyGains();$('#mute').classList.toggle('on',muted);setOnAir();broadcastMeta();};
async function setTalk(on){if(!live||on===talking)return;if(on&&!micStream&&!(await openMic()))return;talking=on;applyGains();$('#talk').classList.toggle('on',on);setOnAir();broadcastMeta();}
(function ptt(){const b=$('#talk');let down=0,latched=false;
  b.onpointerdown=e=>{e.preventDefault();b.setPointerCapture(e.pointerId);down=Date.now();if(latched){latched=false;setTalk(false);down=0;}else setTalk(true);};
  const up=()=>{if(!down)return;const held=Date.now()-down;down=0;if(held<350){latched=true;}else setTalk(false);};
  b.onpointerup=up;b.onpointercancel=up;
  addEventListener('keydown',e=>{if(e.key==='t'&&!e.repeat&&!/input|textarea|select/i.test(e.target.tagName))setTalk(true);});
  addEventListener('keyup',e=>{if(e.key==='t'&&!latched&&!/input|textarea|select/i.test(e.target.tagName))setTalk(false);});})();
function stopAll(){live=false;muted=false;talking=false;applyGains();
  for(const [,p] of peers)p.pc.close();peers.clear();render();sig.send('*',{type:'host-stopped'});broadcastMeta();
  $('#idle').hidden=false;$('#live').hidden=true;setOnAir();$('#sttag').textContent='studio';}
$('#stop').onclick=stopAll;
addEventListener('pagehide',()=>{navigator.sendBeacon('/api/msg',JSON.stringify({from:'host',to:'*',data:{type:'host-stopped'}}));});
if(!['localhost','127.0.0.1'].includes(location.hostname))showErr('Open this page as http://localhost:'+location.port+'/host — browsers only allow audio capture on localhost.');
sig.onstale=()=>{   // a newer studio tab was opened: this one steps down so students never talk to two studios
  for(const [,p] of peers)p.pc.close();peers.clear();live=false;applyGains();
  document.body.innerHTML='<div class=wrap style="text-align:center;padding-top:20vh"><div class=station style="justify-content:center;margin-bottom:18px"><span class=logo>'+ICON_WAVE+'</span><span class=stname>CCAST</span></div><p style="color:var(--ink2)">A newer studio tab has taken over. Close this one — the studio only ever runs in one tab.</p></div>';};
setOnAir();renderStrips();fetch('/api/reset?id=host&hid='+HID).then(()=>{sig.send('*',{type:'host-ready',hid:HID});sig.run();});
</script></body></html>"""

LISTEN_CSS = r"""
:root{--paper:#f7f6f2;--card:#fbfaf8;--ink:#3b2d6e;--ink2:#8e7fc4;--ink3:#cfc8e6;--grid:rgba(59,45,110,.14);--live:#2f9e6d;--warn:#b9791d;--off:#c0392b;--screen:#eef0e6}
*{box-sizing:border-box}html,body{height:100%}html{color-scheme:light}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.4 -apple-system,BlinkMacSystemFont,"SF Pro Text",Inter,Segoe UI,Roboto,sans-serif;-webkit-font-smoothing:antialiased;overflow:hidden;position:relative}
.mono{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
svg.bg{position:fixed;inset:0;width:100%;height:100%;z-index:0;pointer-events:none;opacity:.5}
.rx{position:relative;z-index:1;height:100dvh;max-width:1100px;margin:0 auto;padding:14px 16px 14px;display:flex;flex-direction:column;gap:10px}
.rxhead{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:0 4px;flex:none}
.title{display:flex;align-items:baseline;gap:12px}.title .name{font-weight:800;letter-spacing:.14em;font-size:18px;text-transform:uppercase}
.title .sub{font-size:10px;letter-spacing:.16em;color:var(--ink2);text-transform:uppercase}
.readout{font-size:11px;letter-spacing:.08em;color:var(--ink2);text-transform:uppercase;white-space:nowrap}
/* ---- the instrument housing */
.unit{flex:1;min-height:0;border:1.5px solid var(--ink);border-radius:22px;background:var(--card);padding:16px;display:grid;grid-template-columns:minmax(0,1fr) 168px;gap:16px;position:relative;box-shadow:7px 7px 0 -1px var(--paper),7px 7px 0 0 var(--ink3)}
.unit::before,.unit::after,.unit .scr1,.unit .scr2{content:'';position:absolute;width:9px;height:9px;border:1.5px solid var(--ink);border-radius:50%;background:var(--paper)}
.unit::before{left:9px;top:9px}.unit::after{right:9px;top:9px}.unit .scr1{left:9px;bottom:9px}.unit .scr2{right:9px;bottom:9px}
.unit .scr1::after,.unit .scr2::after,.unit::before{background:var(--paper) linear-gradient(var(--ink),var(--ink)) center/1px 5px no-repeat}
.left{display:flex;flex-direction:column;min-height:0;min-width:0;gap:10px}
.crt{flex:1;min-height:0;position:relative;border:2px solid var(--ink);border-radius:14px;padding:6px;background:var(--paper)}
.glass{position:absolute;inset:6px;border:1.5px solid var(--ink);border-radius:10px;overflow:hidden;background:radial-gradient(120% 100% at 50% 40%,#f3f4ec 0%,var(--screen) 60%,#e3e6d8 100%)}
.glass canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.ovl{position:absolute;font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink);font-family:ui-monospace,Menlo,monospace;pointer-events:none}
.ovl:empty,.ovl.hide{display:none}
.ovl.tl{left:12px;top:9px;display:flex;align-items:center;gap:7px}
.ovl.tc{left:50%;top:9px;transform:translateX(-50%);display:flex;align-items:center;gap:8px}.ovl.tc i{width:7px;height:7px;border-radius:50%;border:1.5px solid var(--ink);display:inline-block}.ovl.tc i.on{background:var(--ink)}.ovl.tc i.one{border-color:var(--live)}.ovl.tc i.one.on{background:var(--live)}.ovl.tr{right:12px;top:9px;color:var(--ink2)}
.ovl.bl{left:12px;bottom:8px;max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-transform:none;letter-spacing:.02em;font-weight:600;font-family:inherit;font-size:12px}
.ovl.br{right:12px;bottom:8px;color:var(--ink2)}
.dot{width:8px;height:8px;border-radius:50%;border:1.5px solid var(--ink);flex:none}
.dot.live{background:var(--live);border-color:var(--live);box-shadow:0 0 0 3px rgba(47,158,109,.18)}.dot.wait{background:var(--warn);border-color:var(--warn)}.dot.off{background:var(--off);border-color:var(--off)}
/* ---- meters right under the screen */
.vu{flex:none;border:1.5px solid var(--ink);border-radius:10px;padding:8px 12px 6px;background:var(--paper)}
.scale{position:relative;height:12px;margin:0 6px 4px 26px;font-size:9px;color:var(--ink2);font-family:ui-monospace,Menlo,monospace}.scale span{position:absolute;transform:translateX(-50%)}
.meter{display:grid;grid-template-columns:16px 1fr;gap:10px;align-items:center;font-size:10px;color:var(--ink2);font-weight:700;margin:3px 0}
.bar{height:11px;border:1.5px solid var(--ink);border-radius:6px;position:relative;overflow:hidden;background:repeating-linear-gradient(90deg,transparent 0 9.6%,var(--grid) 9.6% 10%),linear-gradient(90deg,transparent 90%,rgba(192,57,43,.10) 90%)}
.bar i{position:absolute;inset:0;width:0;background:linear-gradient(90deg,var(--ink2),var(--ink) 80%,var(--warn) 92%,var(--off) 98%);transition:width .04s linear}
.bar b{position:absolute;top:0;bottom:0;width:2px;background:var(--ink);left:0;transition:left .1s}
.sub2{flex:none;color:var(--ink2);font-size:12px;min-height:16px;padding:0 4px}
/* ---- cue mixer */
.mixer{flex:none;display:flex;gap:6px;overflow-x:auto;padding:8px 8px 6px;border:1.5px solid var(--ink);border-radius:10px;background:var(--paper)}
.cstrip{flex:1;min-width:72px;display:flex;flex-direction:column;align-items:center;gap:2px}
.knob.sm{width:56px;height:56px}.knob.sm .val{font-size:9px;bottom:-4px}
.cname{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink);font-weight:700;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:6px}
.cbtns{display:flex;gap:4px;margin-top:3px}.cbtns button{width:26px;height:22px;border:1.5px solid var(--ink);border-radius:6px;background:transparent;color:var(--ink);font:700 10px/1 inherit;cursor:pointer;padding:0;display:grid;place-items:center}
.cstrip.muted .cm{background:var(--warn);border-color:var(--warn);color:#fff}.cstrip.solo .cs{background:var(--live);border-color:var(--live);color:#fff}
.cstrip.muted .knob{opacity:.4}
/* ---- control column */
.ctrl{display:flex;flex-direction:column;align-items:center;justify-content:space-between;gap:8px;padding:2px 0}
.plate{width:100%;text-align:center;font-size:9.5px;letter-spacing:.2em;color:var(--ink2);text-transform:uppercase;border-bottom:1.5px dashed var(--ink3);padding-bottom:6px}
.lbl{font-size:9.5px;letter-spacing:.18em;text-transform:uppercase;color:var(--ink2);text-align:center}
.knob{width:92px;height:92px;position:relative;cursor:ns-resize;touch-action:none;user-select:none}
.knob svg{width:100%;height:100%;display:block}
.knob .val{position:absolute;left:0;right:0;bottom:-2px;text-align:center;font-size:11px;color:var(--ink);font-family:ui-monospace,Menlo,monospace}
.round{width:64px;height:64px;border-radius:50%;border:1.5px solid var(--ink);background:var(--card);color:var(--ink);display:grid;place-items:center;cursor:pointer;transition:.15s;box-shadow:inset 0 0 0 4px var(--card),inset 0 0 0 5.5px var(--ink3)}
.round svg{width:24px;height:24px}.round:hover{background:rgba(59,45,110,.06)}.round:active{transform:translateY(1px)}
.round.on{background:var(--ink);color:#fff;box-shadow:inset 0 0 0 4px var(--ink),inset 0 0 0 5.5px #fff}
.round.mute{width:48px;height:48px}.round.mute svg{width:18px;height:18px}.round.mute.on{background:var(--warn);border-color:var(--warn);box-shadow:inset 0 0 0 3px var(--warn),inset 0 0 0 4.5px #fff}
.pair{display:flex;gap:14px;align-items:center}
.btnlbl{display:flex;flex-direction:column;align-items:center;gap:6px}
.sw{position:relative;width:128px;height:28px;border:1.5px solid var(--ink);border-radius:14px;background:var(--paper);cursor:pointer;display:grid;grid-template-columns:1fr 1fr;font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);align-items:center;text-align:center;font-weight:700}
.sw::before{content:'';position:absolute;top:2px;bottom:2px;width:calc(50% - 3px);left:2px;border-radius:11px;background:var(--ink);transition:left .15s}
.sw.fast::before{left:calc(50% + 1px)}.sw span{position:relative;z-index:1}.sw:not(.fast) span:first-child,.sw.fast span:last-child{color:#fff}
.unmute{position:fixed;inset:0;background:rgba(247,246,242,.9);display:none;place-items:center;z-index:9}.unmute.show{display:grid}
.unmute button{font:inherit;font-weight:800;letter-spacing:.1em;text-transform:uppercase;border:1.5px solid var(--ink);background:var(--ink);color:#fff;border-radius:14px;padding:20px 40px;cursor:pointer}
.toast{position:fixed;left:50%;bottom:20px;transform:translateX(-50%) translateY(20px);opacity:0;background:var(--ink);color:#fff;padding:10px 16px;border-radius:12px;transition:.25s;pointer-events:none;font-weight:600;z-index:9}.toast.show{opacity:1;transform:translateX(-50%)}
/* phones: control column becomes a row under the meters */
@media(max-width:640px){.unit{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) auto;padding:12px}.ctrl{flex-direction:row;flex-wrap:wrap;justify-content:space-around;gap:10px 14px;padding:0}.plate{display:none}.knob{width:84px;height:84px}.ovl.br,.ovl.tr{display:none}}
"""

LISTEN_HTML = r"""<!doctype html><html><head><meta charset=utf-8><title>CCAST</title>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover"><style>%LISTEN_CSS%</style></head><body>
%BG%
<main class=rx>
 <header class=rxhead>
  <div class=title><span class=name id=station>CCAST</span><span class=sub>receiver · rx‑1</span></div>
  <div class="readout mono" id=lat>—</div>
 </header>
 <section class=unit><span class=scr1></span><span class=scr2></span>
  <div class=left>
   <div class=crt><div class=glass>
    <canvas id=grat></canvas><canvas id=beam></canvas>
    <div class="ovl tl"><span class=dot id=dot></span><span id=st>Off air</span></div>
    <div class="ovl tr" id=s1></div>
    <div class="ovl tc" id=tc></div>
    <div class="ovl bl hide" id=now></div>
    <div class="ovl br" id=s2>2.5 ms/div</div>
   </div></div>
   <div class=vu>
    <div class=scale id=scale></div>
    <div class=meter>L<div class=bar id=mL><i></i><b></b></div></div>
    <div class=meter>R<div class=bar id=mR><i></i><b></b></div></div>
   </div>
   <div class=mixer id=mixer hidden></div>
   <div class=sub2 id=sub>Press the power button to tune in.</div>
  </div>
  <div class=ctrl>
   <div class=plate>ccast · rx‑1</div>
   <div class=btnlbl>
    <div class=knob id=knob title="Drag up/down or scroll · double-click resets"></div>
    <span class=lbl>volume</span>
   </div>
   <div class=pair>
    <div class=btnlbl><button class=round id=btn title="Listen / stop"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 3v9"/><path d="M6.3 6.6a8 8 0 1 0 11.4 0"/></svg></button><span class=lbl>listen</span></div>
    <div class=btnlbl><button class="round mute" id=lmute title="Mute on this computer only"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 9l5 6M21 9l-5 6"/></svg></button><span class=lbl>mute</span></div>
   </div>
   <div class=pair>
    <div class=btnlbl><button class="round mute" id=clk title="Click track — follows the DAW transport"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v7"/><path d="M6 21l3-10h6l3 10z"/><path d="M12 10l5-5"/></svg></button><span class=lbl>click</span></div>
    <div class=btnlbl><div class="knob sm" id=clkknob title="Click level"></div><span class=lbl>level</span></div>
   </div>
   <div class=btnlbl><div class=sw id=snd title="Click sound"><span>stick</span><span>shaker</span></div><span class=lbl>sound</span></div>
   <div class=btnlbl><div class=sw id=seg title="Jitter buffer: steady 150 ms, or as low as the network allows"><span>smooth</span><span>low lat</span></div><span class=lbl>buffer</span></div>
  </div>
 </section>
</main>
<div class=unmute id=unmute><button id=unmuteBtn>Tap to hear</button></div>
<div id=sinks hidden></div>
<div class=toast id=toast></div>
<script>%JS%
const me='s-'+rnd();const sig=new Signal(me);
let pc=null,listening=false,timer=null,pending=[],attempt=0,lmuted=false;
const meta={station:'CCAST',now:'',live:false,muted:false,talking:false};
// ---------- playback bus (Web Audio): every received channel -> its fader -> master -> speakers
const AC=window.AudioContext||window.webkitAudioContext;const rac=new AC({latencyHint:'interactive'});
const master=rac.createGain();master.connect(rac.destination);const bus=rac.createGain();bus.connect(master);   // bus = pre-master sum for meters/scope
const split=rac.createChannelSplitter(2);bus.connect(split);const N=2048,bufL=new Float32Array(N),bufR=new Float32Array(N);
const an=rac.createAnalyser(),anR=rac.createAnalyser();an.fftSize=anR.fftSize=N;an.smoothingTimeConstant=anR.smoothingTimeConstant=0;split.connect(an,0);split.connect(anR,1);
const chans=new Map();   // stemId -> {id,name,kind,gain,src,el,level,muted,solo}
let receivers=[];
function ramp(g,v,ms){const t=rac.currentTime;g.gain.cancelScheduledValues(t);g.gain.setValueAtTime(g.gain.value,t);g.gain.linearRampToValueAtTime(v,t+ms/1000);}
// ---------- buffer switch
let mode='smooth';try{mode=localStorage.cc_mode||'smooth';}catch(e){}
// Chrome's jitter buffer is tuned for speech and time-stretches to chase latency, which warbles on music.
// A fixed target keeps it steady. Low latency: 20 ms floor — NetEq still rises above it if the network gets jittery.
function applyMode(){const ms=mode==='smooth'?150:20;for(const r of receivers){try{r.jitterBufferTarget=ms;}catch(e){}try{if('playoutDelayHint' in r)r.playoutDelayHint=ms/1000;}catch(e){}}$('#seg').classList.toggle('fast',mode==='fast');}
$('#seg').onclick=()=>{mode=mode==='smooth'?'fast':'smooth';try{localStorage.cc_mode=mode;}catch(e){}applyMode();};applyMode();
// ---------- knobs (0..1, perceptual: gain = v^2) — drag vertically or scroll, double-click resets
function makeKnob(el,v,onchange,size){el.innerHTML=`<svg viewBox="0 0 100 100" fill="none" stroke="#3b2d6e" stroke-width="1.5"><g class=ticks></g><circle cx="50" cy="50" r="30" fill="#fbfaf8"/><circle cx="50" cy="50" r="24" stroke-dasharray="1.5 3.2" opacity=".6"/><g class=ind><line x1="50" y1="50" x2="50" y2="24" stroke-width="2.5" stroke-linecap="round"/></g></svg><div class="val mono"></div>`;
  const ticks=el.querySelector('.ticks'),ind=el.querySelector('.ind'),val=el.querySelector('.val');
  for(let i=0;i<=10;i++){const a=(-135+i*27)*Math.PI/180,r1=38,r2=i%5?41:44;const t=document.createElementNS('http://www.w3.org/2000/svg','line');
    t.setAttribute('x1',50+r1*Math.sin(a));t.setAttribute('y1',50-r1*Math.cos(a));t.setAttribute('x2',50+r2*Math.sin(a));t.setAttribute('y2',50-r2*Math.cos(a));t.setAttribute('opacity',i%5?'.5':'1');ticks.append(t);}
  const k={get v(){return v;},set(nv){v=Math.min(1,Math.max(0,nv));ind.setAttribute('transform',`rotate(${-135+v*270} 50 50)`);const db=v<=0?-Infinity:40*Math.log10(v);val.textContent=db===-Infinity?'−∞':(db>=-0.05?'0':db.toFixed(1).replace('-','−'))+(size?'':' dB');onchange(v);}};
  let y0=null,v0=0;el.onpointerdown=e=>{el.setPointerCapture(e.pointerId);y0=e.clientY;v0=v;};el.onpointermove=e=>{if(y0===null)return;k.set(v0+(y0-e.clientY)/160);};
  el.onpointerup=el.onpointercancel=()=>{y0=null;};el.onwheel=e=>{e.preventDefault();k.set(v-Math.sign(e.deltaY)*0.03);};el.ondblclick=()=>k.set(1);k.set(v);return k;}
let vol=1;try{vol=parseFloat(localStorage.cc_vol);if(isNaN(vol))vol=1;}catch(e){}
const volKnob=makeKnob($('#knob'),vol,v=>{vol=v;ramp(master,lmuted?0:v*v,30);try{localStorage.cc_vol=v;}catch(e){}});
// ---------- cue mixer (appears when the studio sends more than one channel)
let levels={};try{levels=JSON.parse(localStorage.cc_levels||'{}');}catch(e){}
function chanGain(c){if(c.kind==='tb')return 1;const anySolo=[...chans.values()].some(x=>x.kind!=='tb'&&x.solo);const audible=!c.muted&&(!anySolo||c.solo);return audible?c.level*c.level:0;}
function applyMix(){const active={};for(const c of chans.values()){ramp(c.gain,chanGain(c),40);if(c.kind!=='tb')active[c.id]=chanGain(c)>0;}
  clearTimeout(applyMix.t);applyMix.t=setTimeout(()=>sig.send('host',{type:'active',active}),250);   // tell the studio which channels to actually send
  for(const c of chans.values())if(c.el){c.el.classList.toggle('muted',c.muted);c.el.classList.toggle('solo',c.solo);}}
function renderMixer(){const list=[...chans.values()].filter(c=>c.kind!=='tb');const mx=$('#mixer');mx.hidden=list.length<2;mx.innerHTML='';if(list.length<2)return;
  for(const c of list){const el=document.createElement('div');el.className='cstrip';el.innerHTML=`<div class="knob sm"></div><div class=cname title="${c.name}">${c.name}</div><div class=cbtns><button class=cm title="Mute">M</button><button class=cs title="Solo">S</button></div>`;
    c.el=el;makeKnob(el.querySelector('.knob'),c.level,v=>{c.level=v;levels[c.name]=v;try{localStorage.cc_levels=JSON.stringify(levels);}catch(e){}applyMix();},true);
    el.querySelector('.cm').onclick=()=>{c.muted=!c.muted;applyMix();};el.querySelector('.cs').onclick=()=>{c.solo=!c.solo;applyMix();};mx.append(el);}
  applyMix();}
function addChan(info,stream){const g=rac.createGain();g.gain.value=0;const src=rac.createMediaStreamSource(stream);src.connect(g);g.connect(bus);
  // Chrome only delivers remote WebRTC audio into Web Audio while the stream is also attached to a media element
  const el=document.createElement('audio');el.srcObject=stream;el.muted=true;el.autoplay=true;el.playsInline=true;$('#sinks').append(el);el.play().catch(()=>{});
  const c={id:info.id,name:info.name,kind:info.kind,gain:g,src,sink:el,level:info.kind==='tb'?1:(levels[info.name]!=null?levels[info.name]:1),muted:false,solo:false};chans.set(c.id,c);ramp(g,chanGain(c),60);}
function clearChans(){for(const c of chans.values()){try{c.src.disconnect();}catch(e){}c.sink.remove();}chans.clear();$('#mixer').hidden=true;$('#mixer').innerHTML='';}
// ---------- state
const connected=()=>pc&&pc.connectionState==='connected';let scopeMode='off';
function setState(cls,head,sub){$('#dot').className='dot '+cls;$('#st').textContent=head;$('#sub').textContent=sub||'';scopeMode=cls==='live'?'live':cls==='wait'?'wait':'off';}
function renderMeta(){$('#station').textContent=meta.station;document.title=meta.station;const n=meta.now||(meta.live&&connected()?'Live from the studio':'');$('#now').textContent=n;$('#now').classList.toggle('hide',!n);if(connected())showLive();}
function showLive(){if(lmuted)setState('wait','Muted here','Press mute again to unmute');else if(meta.talking)setState('live','Teacher talking','');else if(meta.muted)setState('wait','Muted by the teacher','It comes back automatically');else setState('live','On air',chans.size>2?'Your own cue mix — knobs, mute and solo per channel':'');}
$('#lmute').onclick=()=>{lmuted=!lmuted;ramp(master,lmuted?0:vol*vol,30);$('#lmute').classList.toggle('on',lmuted);if(connected())showLive();};
function schedule(ms){clearTimeout(timer);timer=setTimeout(()=>{if(listening&&!connected())hello();},ms);}
function hello(){if(!listening)return;attempt++;sig.send('host',{type:'hello'});setState('wait','Tuning in…','Looking for the studio');schedule(Math.min(15000,4000+attempt*2000));}
function teardown(){if(pc){pc.onconnectionstatechange=null;pc.close();pc=null;}receivers=[];pending=[];if(dc){clearInterval(dc._pinger);dc=null;}clk.line=null;clk.running=false;clk.offset=null;clk.bestRtt=Infinity;clk.seq=-1;killClicks();renderClock();lastBytes=lastT=lastJbD=lastJbN=0;clearChans();$('#lat').textContent='—';$('#s1').textContent='';renderMeta();}
// ---------- the oscilloscope + meters on the receive bus
(function buildScale(){const el=$('#scale');[-60,-48,-36,-24,-18,-12,-6,-3,0].forEach(db=>{const t=document.createElement('span');t.style.left=((db+60)/60*100)+'%';t.textContent=db===0?'0':db;el.append(t);});})();
const gr=$('#grat'),gx=gr.getContext('2d'),bm=$('#beam'),bx=bm.getContext('2d');let W=0,H=0,D=1;
function graticule(){const r=gr.getBoundingClientRect();D=devicePixelRatio||1;W=Math.round(r.width*D);H=Math.round(r.height*D);if(!W||!H)return;
  gr.width=bm.width=W;gr.height=bm.height=H;gx.clearRect(0,0,W,H);gx.strokeStyle='rgba(59,45,110,.18)';gx.lineWidth=D;
  for(let i=1;i<10;i++){const x=Math.round(W*i/10)+.5;gx.beginPath();gx.moveTo(x,0);gx.lineTo(x,H);gx.stroke();}
  for(let i=1;i<8;i++){const y=Math.round(H*i/8)+.5;gx.beginPath();gx.moveTo(0,y);gx.lineTo(W,y);gx.stroke();}
  gx.strokeStyle='rgba(59,45,110,.45)';const cy=Math.round(H/2)+.5,cxm=Math.round(W/2)+.5;gx.beginPath();gx.moveTo(0,cy);gx.lineTo(W,cy);gx.moveTo(cxm,0);gx.lineTo(cxm,H);gx.stroke();
  gx.beginPath();for(let i=0;i<=50;i++){const x=W*i/50,l=(i%5?3:6)*D;gx.moveTo(x,cy-l);gx.lineTo(x,cy+l);}for(let i=0;i<=40;i++){const y=H*i/40,l=(i%5?3:6)*D;gx.moveTo(cxm-l,y);gx.lineTo(cxm+l,y);}gx.stroke();
  const v=gx.createRadialGradient(W/2,H/2,Math.min(W,H)*.45,W/2,H/2,Math.max(W,H)*.75);v.addColorStop(0,'rgba(59,45,110,0)');v.addColorStop(1,'rgba(59,45,110,.08)');gx.fillStyle=v;gx.fillRect(0,0,W,H);}
new ResizeObserver(graticule).observe(gr);graticule();
const bars=[$('#mL'),$('#mR')],peak=[0,0];let t0=performance.now();
function draw(){
  if(connected()){[an,anR].forEach((a,i)=>{const b=i?bufR:bufL;a.getFloatTimeDomainData(b);let s=0;for(let k=0;k<N;k++)s+=b[k]*b[k];
    const db=20*Math.log10(Math.sqrt(s/N)+1e-9);const pct=Math.max(0,Math.min(100,(db+60)/60*100));peak[i]=Math.max(pct,peak[i]-0.6);
    bars[i].querySelector('i').style.width=pct+'%';bars[i].querySelector('b').style.left=peak[i]+'%';});}
  if(W&&H){bx.globalCompositeOperation='destination-out';bx.fillStyle='rgba(0,0,0,.28)';bx.fillRect(0,0,W,H);bx.globalCompositeOperation='source-over';   // phosphor persistence
    const t=(performance.now()-t0)/1000;bx.shadowColor='rgba(59,45,110,.55)';bx.shadowBlur=6*D;bx.lineJoin='round';
    if(connected()&&scopeMode!=='off'&&!lmuted){let start=0;for(let k=1;k<N/3;k++){if(bufL[k-1]<0&&bufL[k]>=0){start=k;break;}}
      const span=1200,amp=H*0.42;const trace=(b,c,w)=>{bx.strokeStyle=c;bx.lineWidth=w*D;bx.beginPath();for(let k=0;k<span;k++){const x=W*k/span,y=H/2-b[start+k]*amp;k?bx.lineTo(x,y):bx.moveTo(x,y);}bx.stroke();};
      trace(bufR,'rgba(142,127,196,.85)',1.2);trace(bufL,'#3b2d6e',1.7);}
    else if(scopeMode==='wait'){const x=((t*0.4)%1)*W;bx.fillStyle='#3b2d6e';bx.beginPath();bx.arc(x,H/2,3*D,0,7);bx.fill();}
    else{bx.strokeStyle='#8e7fc4';bx.lineWidth=1.3*D;bx.beginPath();for(let k=0;k<160;k++){const x=W*k/159,y=H/2+(Math.sin(k*1.7+t*3)*0.35+Math.sin(k*0.31-t)*0.25)*D;k?bx.lineTo(x,y):bx.moveTo(x,y);}bx.stroke();}
    bx.shadowBlur=0;}
  requestAnimationFrame(draw);}
draw();
// ---------- click track: the DAW's MIDI clock arrives via the data channel; we schedule a local click on the beat,
// delayed by the measured stream latency so it lands on the music the student actually hears.
const clk={on:false,sound:'stick',level:0.6,line:null,running:false,bpm:0,bpb:4,offset:null,bestRtt:Infinity,lastBeat:-1,seq:-1,nodes:[]};
try{clk.sound=localStorage.cc_snd||'stick';clk.level=parseFloat(localStorage.cc_clk);if(isNaN(clk.level))clk.level=0.6;}catch(e){}
const clickGain=rac.createGain();clickGain.gain.value=0;clickGain.connect(bus);
const noiseBuf=(()=>{const b=rac.createBuffer(1,rac.sampleRate*0.3,rac.sampleRate);const d=b.getChannelData(0);for(let i=0;i<d.length;i++)d[i]=Math.random()*2-1;return b;})();
function acTime(tPerf){const ts=rac.getOutputTimestamp?rac.getOutputTimestamp():null;if(ts&&ts.contextTime!=null)return ts.contextTime+(tPerf-ts.performanceTime)/1000;return rac.currentTime+(tPerf-performance.now())/1000;}
function stick(t,accent){const n=rac.createBufferSource();n.buffer=noiseBuf;const bp=rac.createBiquadFilter();bp.type='bandpass';bp.frequency.value=accent?2300:1900;bp.Q.value=5;
  const g=rac.createGain();g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(accent?1.0:0.7,t+0.0015);g.gain.exponentialRampToValueAtTime(0.0001,t+0.04);
  n.connect(bp).connect(g).connect(clickGain);n.start(t);n.stop(t+0.06);
  const o=rac.createOscillator();o.frequency.setValueAtTime(accent?920:780,t);o.frequency.exponentialRampToValueAtTime(accent?700:600,t+0.03);const og=rac.createGain();og.gain.setValueAtTime(0.0001,t);og.gain.exponentialRampToValueAtTime(0.35,t+0.001);og.gain.exponentialRampToValueAtTime(0.0001,t+0.03);
  o.connect(og).connect(clickGain);o.start(t);o.stop(t+0.05);clk.nodes.push(n,o);}
function shaker(t,accent){const n=rac.createBufferSource();n.buffer=noiseBuf;const hp=rac.createBiquadFilter();hp.type='highpass';hp.frequency.value=3200;const bp=rac.createBiquadFilter();bp.type='bandpass';bp.frequency.value=accent?7500:6200;bp.Q.value=0.9;
  const g=rac.createGain();g.gain.setValueAtTime(0.0001,t);g.gain.exponentialRampToValueAtTime(accent?0.9:0.6,t+0.008);g.gain.exponentialRampToValueAtTime(0.0001,t+(accent?0.12:0.08));
  n.connect(hp).connect(bp).connect(g).connect(clickGain);n.start(t);n.stop(t+0.15);clk.nodes.push(n);}
function killClicks(){for(const n of clk.nodes){try{n.stop();}catch(e){}}clk.nodes=[];}
let latSec=0.06;   // running estimate of stream latency (s), updated by the stats loop
function beatAt(tLocal){const l=clk.line;return l.beat+(tLocal-l.tLocal)/60000*l.bpm;}
setInterval(()=>{if(!(clk.on&&clk.running&&clk.line&&clk.offset!=null&&rac.state==='running'))return;
  const now=performance.now(),horizon=now+220,per=60000/clk.line.bpm;
  let n=Math.max(clk.lastBeat+1,Math.ceil(beatAt(now-latSec*1000-30)));
  for(;;){const tBeat=clk.line.tLocal+(n-clk.line.beat)*per+latSec*1000;if(tBeat>horizon)break;
    if(tBeat>now-20){const at=Math.max(acTime(tBeat),rac.currentTime+0.005);const accent=((n%clk.bpb)+clk.bpb)%clk.bpb===0;(clk.sound==='shaker'?shaker:stick)(at,accent);}
    clk.lastBeat=n;n++;if(n-clk.lastBeat>64)break;}
  if(clk.nodes.length>40)clk.nodes.splice(0,clk.nodes.length-40);},40);
function onClockMsg(m){if(m.seq<=clk.seq)return;clk.seq=m.seq;const wasRunning=clk.running;clk.running=m.running;clk.bpm=m.bpm;clk.bpb=m.bpb||4;
  if(clk.offset!=null&&m.bpm>0){let tLocal=m.t-clk.offset;
    if(clk.line&&wasRunning){const pred=beatAt(tLocal);const err=pred-m.beat;                       // beats we are ahead of the studio
      if(Math.abs(err)>0.08){clk.lastBeat=Math.floor(m.beat)-1;}                                   // real jump (locate / restart): resync
      else{tLocal-=err/m.bpm*60000*0.75;}                                                         // small drift: take only a quarter of the correction (phase-lock)
    }else clk.lastBeat=Math.floor(m.beat)-1;
    clk.line={beat:m.beat,tLocal,bpm:m.bpm};}
  if(!m.running){killClicks();clk.line=clk.line;}renderClock();}
let dc=null;function wireDC(ch){dc=ch;ch.onmessage=e=>{try{const m=JSON.parse(e.data);
    if(m.type==='pong'){const t3=performance.now(),rtt=t3-m.t1,off=m.t2-(m.t1+t3)/2;if(rtt<=clk.bestRtt*1.5||clk.offset==null){clk.offset=clk.offset==null?off:clk.offset*0.7+off*0.3;}clk.bestRtt=Math.min(clk.bestRtt*1.02,rtt);}
    else if(m.type==='clock')onClockMsg(m);}catch(err){}};
  const ping=()=>{if(ch.readyState==='open')try{ch.send(JSON.stringify({type:'ping',t1:performance.now()}));if(!clk.bpm)ch.send(JSON.stringify({type:'clock?'}));}catch(e){}};ch.onopen=()=>{ping();};setTimeout(ping,300);ch._pinger=setInterval(ping,1000);ch.onclose=()=>clearInterval(ch._pinger);}
function renderClock(){const el=$('#tc');if(!clk.bpm){el.innerHTML='';return;}
  let dots='';for(let i=0;i<clk.bpb;i++)dots+=`<i class="${i===0?'one':''}"></i>`;el.innerHTML=`<span>${clk.running?'▶':'■'} ${clk.bpm.toFixed(1)}</span>${dots}`;}
setInterval(()=>{if(!clk.line||!clk.running||clk.offset==null)return;const b=Math.floor(beatAt(performance.now()-latSec*1000));const i=((b%clk.bpb)+clk.bpb)%clk.bpb;[...$('#tc').querySelectorAll('i')].forEach((d,k)=>d.classList.toggle('on',k===i));},50);
function applyClick(){ramp(clickGain,clk.on?clk.level*clk.level*0.9:0,30);$('#clk').classList.toggle('on',clk.on);$('#snd').classList.toggle('fast',clk.sound==='shaker');if(!clk.on)killClicks();}
$('#clk').onclick=()=>{clk.on=!clk.on;rac.resume().catch(()=>{});if(clk.on&&clk.line)clk.lastBeat=Math.floor(beatAt(performance.now()-latSec*1000));applyClick();
  if(clk.on&&!clk.bpm)toast(dc&&dc.readyState==='open'?'No DAW clock yet — the studio needs MIDI Clock on its IAC input, and the DAW must be playing':'Not connected to the studio’s clock — reload this page');};
$('#snd').onclick=()=>{clk.sound=clk.sound==='stick'?'shaker':'stick';try{localStorage.cc_snd=clk.sound;}catch(e){}applyClick();};
makeKnob($('#clkknob'),clk.level,v=>{clk.level=v;try{localStorage.cc_clk=v;}catch(e){}applyClick();},true);applyClick();
// ---------- signalling
let stemInfo=[];
sig.onmsg=async(from,d)=>{if(from!=='host')return;
  if(d.type==='meta'){Object.assign(meta,d);renderMeta();return;}
  if(!listening)return;
  if(d.type==='wait'){setState('wait','Studio is off air','You’ll be connected automatically when it goes live');schedule(6000);}
  else if(d.type==='host-ready'){if(!(connected()&&pc.hid===d.hid))hello();}
  else if(d.type==='host-stopped'){teardown();setState('wait','Studio went off air','You’ll reconnect automatically when it starts again');schedule(8000);}
  else if(d.type==='stems'){stemInfo=d.stems||[];for(const i of stemInfo){const c=chans.get(i.id);if(c)c.name=i.name;}renderMixer();}
  else if(d.type==='offer'){teardown();stemInfo=d.stems||[];pc=new RTCPeerConnection({iceServers:[]});pc.cid=d.cid;pc.hid=d.hid;
    pc.ontrack=e=>{const mid=e.transceiver.mid;const info=stemInfo.find(s=>s.mid===mid)||{id:'m'+mid,name:'Channel',kind:'ch'};receivers.push(e.receiver);applyMode();
      addChan(info,e.streams[0]);renderMixer();rac.resume().then(()=>$('#unmute').classList.remove('show')).catch(()=>$('#unmute').classList.add('show'));};
    pc.ondatachannel=e=>wireDC(e.channel);
    pc.onicecandidate=e=>{if(e.candidate)sig.send('host',{type:'ice',cid:pc.cid,c:e.candidate});};
    pc.onconnectionstatechange=()=>{const s=pc.connectionState;
      if(s==='connected'){attempt=0;clearTimeout(timer);showLive();renderMeta();if(rac.state!=='running')$('#unmute').classList.add('show');}
      else if(s==='disconnected'){setState('wait','Reconnecting…','');schedule(3000);}
      else if(s==='failed'){setState('wait','Reconnecting…','');hello();}};
    await pc.setRemoteDescription(d.sdp);const a=await pc.createAnswer();a.sdp=stereo(a.sdp);await pc.setLocalDescription(a);
    sig.send('host',{type:'answer',cid:pc.cid,sdp:pc.localDescription});
    for(const c of pending)await pc.addIceCandidate(c).catch(()=>{});pending=[];schedule(12000);}
  else if(d.type==='ice'){if(pc&&pc.cid===d.cid){if(pc.remoteDescription)await pc.addIceCandidate(d.c).catch(()=>{});else pending.push(d.c);}else pending.push(d.c);}};
sig.onreset=()=>{if(!listening)return;if(connected())showLive();else hello();};   // media is peer-to-peer: a server restart doesn't touch a live connection
sig.onoffline=()=>{if(listening&&!connected())setState('off','Studio not reachable','Are you on the same Wi-Fi? Retrying…');};
addEventListener('online',()=>{if(listening)hello();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&listening&&!connected())hello();});
$('#unmuteBtn').onclick=()=>{rac.resume().then(()=>$('#unmute').classList.remove('show')).catch(()=>{});};
$('#btn').onclick=()=>{if(!listening){listening=true;rac.resume().catch(()=>{});$('#btn').classList.add('on');attempt=0;hello();}
  else{listening=false;clearTimeout(timer);teardown();sig.send('host',{type:'bye'});$('#btn').classList.remove('on');setState('','Off air','Press the power button to tune in again.');}};
addEventListener('pagehide',()=>{if(listening)navigator.sendBeacon('/api/msg',JSON.stringify({from:me,to:'host',data:{type:'bye'}}));});
// ---------- readouts + end-to-end latency estimate
let lastBytes=0,lastT=0,lastJbD=0,lastJbN=0;
setInterval(async()=>{if(!connected())return;try{const st=await pc.getStats();let rtt=null,lost=0,jbD=0,jbN=0,bytes=0,codec='',poD=0,poN=0,n=0;
  st.forEach(r=>{if(r.type==='candidate-pair'&&r.nominated&&r.currentRoundTripTime!=null)rtt=r.currentRoundTripTime;
    if(r.type==='inbound-rtp'&&r.kind==='audio'){n++;lost+=r.packetsLost||0;bytes+=r.bytesReceived||0;jbD+=r.jitterBufferDelay||0;jbN+=r.jitterBufferEmittedCount||0;}
    if(r.type==='media-playout'){poD+=r.totalPlayoutDelay||0;poN+=r.totalSamplesCount||0;}
    if(r.type==='codec'&&/opus/i.test(r.mimeType))codec='Opus '+(r.channels===2?'stereo':'mono');});
  const dN=jbN-lastJbN,dD=jbD-lastJbD;const jb=dN>0?dD/dN:null;lastJbN=jbN;lastJbD=jbD;
  const now=performance.now();const kbps=lastT?Math.round((bytes-lastBytes)*8/((now-lastT)/1000)/1000):0;lastBytes=bytes;lastT=now;
  const out=(rac.outputLatency||0)+(rac.baseLatency||0);   // the audible path is Web Audio: jitter buffer -> graph -> device
  // estimate: capture ≈10 ms + studio mixer ≈3 ms + 10 ms Opus frame + half the round trip + jitter buffer + this page's output latency
  const est=jb!=null?Math.round((0.010+0.003+0.010+(rtt||0)/2+jb+out)*1000):null;if(est!=null){const e=est/1000;if(Math.abs(e-latSec)>0.015)latSec=e;else latSec=latSec*0.95+e*0.05;}   // click compensation: re-lock only on a real change, otherwise hold steady
  $('#lat').textContent=(est!=null?'≈ '+est+' ms end-to-end · ':'')+(jb!=null?'buffer '+Math.round(jb*1000)+' ms':'')+(rtt!=null?' · net '+Math.round(rtt*1000)+' ms':'')+(lost?' · lost '+lost:'');
  $('#s1').textContent=codec+(n>1?' × '+(n-1)+'+tb':'')+(kbps?' · '+kbps+' kb/s':'');}catch(e){}},2000);
renderMeta();sig.run();
</script></body></html>"""


ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M4 12h1M8 8v8M12 4v16M16 8v8M20 12h1"/></svg>'
PLAY = '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor" style="vertical-align:-3px"><path d="M8 5v14l11-7z"/></svg>'
STOP = '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" style="vertical-align:-2px"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>'
BG = r"""<svg class=bg viewBox="0 0 1000 1000" preserveAspectRatio="xMidYMid slice" fill="none" stroke="#3b2d6e" stroke-width="1">
 <path d="M-50 720 L1050 180" opacity=".35"/><path d="M-50 900 L1050 420" opacity=".2"/>
 <path d="M60 640 C130 560,160 560,220 640 S320 720,380 640" opacity=".45"/>
 <path d="M620 300 C680 220,720 220,780 300 S880 380,940 300" opacity=".3" stroke-dasharray="4 5"/>
 <path d="M700 820 C740 760,770 760,810 820 S880 880,920 820" opacity=".3"/>
 <circle cx="220" cy="640" r="4" fill="#f7f6f2" opacity=".6"/><circle cx="780" cy="300" r="4" fill="#f7f6f2" opacity=".6"/><circle cx="470" cy="452" r="4" fill="#f7f6f2" opacity=".6"/>
 <path d="M300 200 V320" stroke-dasharray="3 5" opacity=".4"/><path d="M860 560 V700" stroke-dasharray="3 5" opacity=".4"/>
</svg>"""
for _k, _v in (("%CSS%", CSS), ("%HOST_CSS%", HOST_CSS), ("%LISTEN_CSS%", LISTEN_CSS), ("%BG%", BG), ("%JS%", JS_COMMON), ("%ICON%", ICON), ("%PLAY%", PLAY), ("%STOP%", STOP)):
    HOST_HTML = HOST_HTML.replace(_k, _v); LISTEN_HTML = LISTEN_HTML.replace(_k, _v)

try:
    QR_JS = open(os.path.join(HERE, "qrcode.min.js"), "rb").read()
except OSError:
    QR_JS = b""   # page still works, just without the QR image


# ----------------------------------------------------------------- server
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass   # client went away; queued messages stay until acknowledged

    def do_GET(self):
        global _host_hid
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, LISTEN_HTML, "text/html")
        if u.path == "/host":
            return self._send(200, HOST_HTML, "text/html")
        if u.path == "/qrcode.min.js":
            return self._send(200, QR_JS, "application/javascript")
        if u.path == "/api/info":
            return self._send(200, json.dumps({"ip": IPS[0], "alts": IPS[1:], "port": PORT, "url": URL, "gen": GEN}))
        if u.path == "/api/poll":
            cid = q.get("id", [""])[0]
            if not cid:
                return self._send(400, "{}")
            if cid == "host" and q.get("hid", [""])[0] != _host_hid:
                return self._send(200, json.dumps({"gen": GEN, "msgs": [], "stale": True}))   # an older studio tab
            after = int(q.get("after", ["0"])[0] or 0)
            return self._send(200, json.dumps({"gen": GEN, "msgs": take(cid, after, POLL_WAIT)}))
        if u.path == "/api/reset":
            cid = q.get("id", ["host"])[0]
            if cid == "host":
                _host_hid = q.get("hid", [""])[0]
                log("studio page opened" + (" (older tabs superseded)" if _host_hid else ""))
            reset(cid); return self._send(200, "{}")
        if u.path == "/api/quit" and self.client_address[0] in ("127.0.0.1", "::1"):
            log("quit from host page"); self._send(200, "{}")
            threading.Thread(target=shutdown, daemon=True).start(); return
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if urlparse(self.path).path != "/api/msg":
            return self._send(404, "{}")
        try:
            m = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            sender, to, data = str(m["from"])[:40], str(m["to"])[:40], m["data"]
            deliver(sender, to, data)
            t = data.get("type") if isinstance(data, dict) else None
            if t == "hello":
                if sender not in _names:
                    _names[sender] = "listener"; log(f"listener joined ({len(_names)} known)")
            elif t == "bye" and sender in _names:
                _names.pop(sender); log("listener left")
            elif t == "host-ready":
                log("host is live")
            elif t == "host-stopped":
                log("host stopped")
        except Exception as e:
            return self._send(400, json.dumps({"error": str(e)}))
        self._send(200, "{}")


RUNFILES = [os.path.join(HERE, ".classcast.pid"), os.path.join(HERE, ".classcast.port")]


def write_runfiles(port):
    for f, v in zip(RUNFILES, (os.getpid(), port)):
        with open(f, "w") as fh:
            fh.write(str(v))


def shutdown():
    deliver("host", "*", {"type": "host-stopped"})
    time.sleep(0.4)
    for f in RUNFILES:
        try: os.remove(f)
        except OSError: pass
    os._exit(0)


def bind(port_wanted):
    for port in range(port_wanted, port_wanted + 10):
        try:
            srv = ThreadingHTTPServer(("0.0.0.0", port), H)
            return srv, port
        except OSError:
            continue
    sys.exit(f"No free port between {port_wanted} and {port_wanted + 9}")


def daemonize():
    """Detach completely (own session, no controlling terminal, stdio -> log).

    The Dock app launches us; without this, macOS would treat the server as
    part of the app's process tree, think the app is still "running", relaunch
    it at login and answer later clicks with "not responding"."""
    if os.fork():
        os._exit(0)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.chdir(HERE)
    logf = open(os.path.join(HERE, "classcast.log"), "ab", buffering=0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0); os.dup2(logf.fileno(), 1); os.dup2(logf.fileno(), 2)


if __name__ == "__main__":
    if DAEMON:
        daemonize()
    srv, PORT = bind(PORT_WANTED)
    srv.daemon_threads = True
    IPS = lan_ips(); URL = f"http://{IPS[0]}:{PORT}"
    write_runfiles(PORT)
    threading.Thread(target=prune, daemon=True).start()
    box = [f"Students open   {URL}", f"You (teacher)   http://localhost:{PORT}/host"]
    w = max(len(b) for b in box) + 4
    print("\n  ┌" + "─" * w + "┐\n  │" + "CCAST".center(w) + "│\n  ├" + "─" * w + "┤")
    for b in box:
        print("  │  " + b.ljust(w - 2) + "│")
    print("  └" + "─" * w + "┘\n  Close this window or press Ctrl-C to stop.\n", flush=True)

    def open_host():
        url = f"http://localhost:{PORT}/host"
        # Prefer Chrome: it captures stereo cleanly and can share system audio.
        if subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True).returncode != 0:
            webbrowser.open(url)
    if os.environ.get("BROWSER") != "none":
        threading.Timer(0.8, open_host).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        shutdown()
