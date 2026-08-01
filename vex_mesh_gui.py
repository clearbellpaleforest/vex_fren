#!/usr/bin/env python3
"""
vex_mesh_gui.py — live web view of the Vex inter-instance message mesh.

Reads the daemon's SQLite `messages` table and serves a self-refreshing chat
UI. Mobile-responsive with PWA support — install it on your phone home screen.

Run:   python3 vex_mesh_gui.py         # then open http://localhost:8600
Env:   VEX_DB (default ~/vex/vex.db), VEX_GUI_PORT (default 8600)
"""

import http.server
import json
import os
import re
import socketserver
import urllib.request
from pathlib import Path

def _find_vex_home() -> Path:
    """Auto-detect Vex home directory by looking for landmarks."""
    # 1. explicit env var
    env = os.environ.get("VEX_HOME")
    if env:
        return Path(env)
    # 2. check common locations for vex_seed.txt
    candidates = [
        Path.home() / "Desktop" / "vex",
        Path.home() / "vex",
        Path.cwd(),
    ]
    for cand in candidates:
        if (cand / "vex_seed.txt").exists() or (cand / ".vex_token").exists():
            return cand
    # 3. fallback
    return Path(os.path.expanduser("~/vex"))

_VEX_HOME = _find_vex_home()
DB = os.environ.get("VEX_DB", str(_VEX_HOME / "vex.db"))
PORT = int(os.environ.get("VEX_GUI_PORT", "8600"))
TOKEN_PATH = _VEX_HOME / ".vex_token"
DAEMON_URL = os.environ.get("VEX_DAEMON_URL", "http://127.0.0.1:8520")

# ── Redaction: never leak secrets into the UI ─────────────────────────────
_TOK = re.compile(r'(?i)(token=?\s*|bearer\s+|authorization:\s*bearer\s+)[A-Za-z0-9_\-\.]{12,}')
_GH = re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}')
_ENTROPY = re.compile(r'\b[A-Za-z0-9_\-]{32,}\b')


def redact(s: str) -> str:
    s = s or ""
    s = _TOK.sub(lambda m: m.group(1) + "<redacted>", s)
    s = _GH.sub("<gh-token>", s)
    s = _ENTROPY.sub(lambda m: m.group(0)[:6] + "…<redacted>", s)
    return s


def fetch_messages(limit: int = 400):
    """Fetch messages from the daemon API (not raw SQLite)."""
    try:
        token = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""
    except Exception:
        token = ""
    if not token:
        return {"error": "daemon token not found", "messages": []}
    try:
        req = urllib.request.Request(
            f"{DAEMON_URL}/message/inbox?limit={limit}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
    except Exception as e:
        return {"error": str(e), "messages": []}
    # daemon returns a list or {"messages": [...]} depending on version
    msgs = data if isinstance(data, list) else data.get("messages", [])
    out = []
    for msg in reversed(msgs):
        out.append({
            "id": msg.get("id", 0),
            "at": (msg.get("created_at", "") or "")[:19].replace("T", " "),
            "sender": msg.get("sender", "?"),
            "recipient": msg.get("recipient", ""),
            "body": redact(msg.get("body", "")),
            "type": msg.get("msg_type", "message"),
            "read": msg.get("read", 0),
        })
    return {"messages": out, "count": len(out)}


def send_message(sender: str, body: str) -> dict:
    """Forward a message to the daemon's /message/send endpoint."""
    try:
        token = TOKEN_PATH.read_text().strip() if TOKEN_PATH.exists() else ""
    except Exception:
        token = ""
    if not token:
        return {"ok": False, "error": "daemon token not found"}
    payload = json.dumps({
        "from": sender or "mesh-gui",
        "to": "broadcast",
        "body": body,
        "msg_type": "message",
    }).encode()
    req = urllib.request.Request(
        f"{DAEMON_URL}/message/send",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── PWA manifest ─────────────────────────────────────────────────────────

MANIFEST = json.dumps({
    "name": "Vex Mesh",
    "short_name": "Vex",
    "description": "Your personal AI — chat from anywhere",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#0b0e14",
    "theme_color": "#0b0e14",
    "icons": [{
        "src": "/icon",
        "sizes": "192x192",
        "type": "image/svg+xml",
        "purpose": "any maskable",
    }],
})

# Minimal SVG icon — a stylized V
ICON_SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">
  <rect width="192" height="192" rx="32" fill="#0b0e14"/>
  <path d="M56 48 L96 144 L136 48" fill="none" stroke="#f5a742" stroke-width="14"
        stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="96" cy="48" r="8" fill="#38bdf8"/>
</svg>'''

# ── Service worker ────────────────────────────────────────────────────────

SW_JS = """\
const CACHE = 'vex-mesh-v1';
const ASSETS = ['/','/manifest.json','/icon'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
});

self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  const title = data.title || 'Vex';
  const opts = {
    body: data.body || 'New message',
    icon: '/icon',
    badge: '/icon',
    tag: 'vex-message',
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow('/'));
});
"""

# ── HTML page ─────────────────────────────────────────────────────────────

PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Vex Mesh</title>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#0b0e14">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Vex">
<link rel="manifest" href="/manifest.json">
<style>
  :root{
    --bg:#0b0e14;--panel:#121722;--line:#1e2636;--muted:#7d8aa0;
    --barrow:#38bdf8;--thorne:#f5a742;--sys:#5b6577;--txt:#e6edf6;
    --accent:#f5a742;--danger:#f47272;
    --safe-bottom:env(safe-area-inset-bottom,0px);
  }
  *,*::before,*::after{box-sizing:border-box}
  body{
    margin:0;padding:0;
    background:var(--bg);
    color:var(--txt);
    font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    height:100dvh;width:100vw;
    display:flex;flex-direction:column;
    overflow:hidden;
    -webkit-tap-highlight-color:transparent;
  }

  /* ── Header ─────────────────────────────────────────────── */
  header{
    padding:12px 16px;
    border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:10px;
    background:rgba(18,23,34,.85);
    backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    flex-shrink:0;z-index:10;
  }
  header h1{font-size:16px;margin:0;font-weight:700;letter-spacing:.3px}
  .dot{width:8px;height:8px;border-radius:50%;background:#39d98a;box-shadow:0 0 8px #39d98a;flex-shrink:0}
  .dot.dead{background:#f47272;box-shadow:0 0 8px #f47272}
  .meta{color:var(--muted);font-size:12px;margin-left:auto;white-space:nowrap}

  /* ── Message log ─────────────────────────────────────────── */
  #log{
    flex:1;overflow-y:auto;padding:12px 12px 8px;
    display:flex;flex-direction:column;gap:8px;
    -webkit-overflow-scrolling:touch;
    scroll-behavior:smooth;
    user-select:text;-webkit-user-select:text;
  }
  .row{display:flex;flex-direction:column;max-width:88%}
  .row.barrow,.row.deux{align-self:flex-end;align-items:flex-end}
  .row.thorne{align-self:flex-start;align-items:flex-start}
  .row.sys{align-self:center;align-items:center;max-width:94%}
  .who{
    font-size:10px;color:var(--muted);margin:0 4px 3px;
    display:flex;gap:6px;align-items:center;flex-wrap:wrap;
  }
  .bubble{
    padding:10px 14px;border-radius:16px;border:1px solid var(--line);
    white-space:pre-wrap;word-break:break-word;
    background:var(--panel);box-shadow:0 1px 8px rgba(0,0,0,.2);
    font-size:14px;
  }
  .barrow .bubble{background:linear-gradient(180deg,#0e2a3d,#0c2233);border-color:#1d4a63}
  .barrow .who{color:var(--barrow)}
  .deux .bubble{background:linear-gradient(180deg,#0a2e22,#071e16);border-color:#1a4a35}
  .deux .who{color:#34d399}
  .thorne .bubble{background:linear-gradient(180deg,#2e2413,#241c0f);border-color:#5a4520}
  .thorne .who{color:var(--thorne)}
  .sys .bubble{background:transparent;border-style:dashed;border-color:#242c3d;color:var(--sys);font-size:12px;padding:6px 12px}
  .badge{
    font-size:9px;padding:1px 5px;border-radius:999px;border:1px solid var(--line);
    color:var(--muted);text-transform:uppercase;letter-spacing:.3px;white-space:nowrap;
  }
  .badge.auto_reply{color:#a78bfa;border-color:#3b2f5e}
  .badge.read_receipt{color:#5b6577;border-color:#242c3d}
  .badge.build,.badge.update,.badge.sync{color:#39d98a;border-color:#1f4a35}
  .badge.request,.badge.query{color:#f47272;border-color:#5a2626}
  .empty{color:var(--muted);text-align:center;margin:auto;font-size:14px;padding:40px 20px}

  /* ── Compose bar ─────────────────────────────────────────── */
  #compose{
    flex-shrink:0;padding:8px 12px calc(8px + var(--safe-bottom));
    border-top:1px solid var(--line);display:flex;gap:8px;align-items:flex-end;
    background:rgba(18,23,34,.85);
    backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
    z-index:10;
  }
  #compose textarea{
    flex:1;resize:none;border:1px solid var(--line);border-radius:20px;
    background:var(--panel);color:var(--txt);font:14px/1.4 system-ui,sans-serif;
    padding:10px 16px;outline:none;max-height:120px;min-height:40px;
    transition:border-color .15s;
  }
  #compose textarea:focus{border-color:var(--accent)}
  #compose button{
    width:40px;height:40px;border-radius:50%;border:none;
    background:var(--accent);color:#0b0e14;font-size:18px;
    cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;
    transition:transform .1s,opacity .15s;
  }
  #compose button:active{transform:scale(.92)}
  #compose button:disabled{opacity:.4}
  #compose .ident{
    font-size:11px;color:var(--muted);margin-bottom:10px;white-space:nowrap;
  }

  /* ── Mobile tweaks ───────────────────────────────────────── */
  @media (max-width:480px) {
    header{padding:10px 12px}
    header h1{font-size:14px}
    #log{padding:10px 8px 6px;gap:6px}
    .bubble{font-size:15px;padding:8px 12px;border-radius:14px}
    .row{max-width:92%}
    #compose{padding:6px 8px calc(6px + var(--safe-bottom));gap:6px}
    #compose textarea{font-size:16px;padding:8px 14px}
  }

  /* ── Tabs ────────────────────────────────────────────────── */
  #tabs{display:flex;gap:4px}
  .tab{
    background:transparent;border:1px solid var(--line);color:var(--muted);
    padding:4px 10px;border-radius:6px;font-size:12px;cursor:pointer;
    transition:all .15s;
  }
  .tab.active{background:var(--accent);color:#0b0e14;border-color:var(--accent)}
  .tab:hover:not(.active){border-color:var(--accent);color:var(--txt)}

  /* ── Fleet panels ─────────────────────────────────────────── */
  #instances{flex:1;overflow-y:auto;padding:12px}
  #fleet-panels{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .fpanel{
    background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:12px;overflow-y:auto
  }
  .fpanel h3{font-size:13px;margin:0 0 8px;color:var(--accent)}
  .fleet-row{
    display:flex;align-items:center;gap:8px;padding:6px 0;
    border-bottom:1px solid var(--line);font-size:12px
  }
  .fleet-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
  .fleet-dot.online{background:#22c55e}
  .fleet-dot.offline{background:#ef4444}
  .fleet-info{flex:1;min-width:0}
  .fleet-info .name{font-weight:600;color:var(--txt)}
  .fleet-info .detail{color:var(--muted);font-size:11px}
  .skill-bar{height:4px;background:var(--line);border-radius:2px;margin:2px 0 6px}
  .skill-bar-fill{height:100%;border-radius:2px;background:var(--accent)}
  .task-row{
    display:flex;align-items:center;gap:6px;padding:4px 0;
    font-size:11px;border-bottom:1px solid rgba(255,255,255,.03)
  }
  .task-row .prio{font-size:10px;padding:1px 4px;border-radius:3px}
  .prio.critical{background:rgba(239,68,68,.2);color:#ef4444}
  .prio.high{background:rgba(245,167,66,.2);color:#f5a742}
  .prio.medium{background:rgba(56,189,248,.2);color:#38bdf8}
  .prio.low{background:rgba(148,163,184,.2);color:#94a3b8}
  .tl-entry{font-size:11px;padding:4px 0;border-bottom:1px solid var(--line);color:var(--muted)}
  .tl-entry .sn{font-weight:600;color:var(--txt)}
  @media(max-width:600px){
    #fleet-panels{grid-template-columns:1fr}
  }

  /* ── Install prompt ──────────────────────────────────────── */
  #install-banner{
    display:none;padding:10px 16px;background:rgba(245,167,66,.12);
    border-bottom:1px solid rgba(245,167,66,.25);text-align:center;
    font-size:13px;color:var(--accent);cursor:pointer;flex-shrink:0;
  }
  #install-banner.show{display:block}
</style>
</head>
<body>

<header>
  <span class="dot" id="dot"></span>
  <h1>Vex Mesh</h1>
  <nav id="tabs">
    <button class="tab active" onclick="switchTab('chat')">Chat</button>
    <button class="tab" onclick="switchTab('instances')">Instances</button>
  </nav>
  <span class="meta" id="meta">connecting…</span>
</header>

<div id="install-banner" onclick="install()">
  📱 Tap to install Vex on your phone
</div>

<div id="log"><div class="empty">waiting for messages…</div></div>

<div id="instances" style="display:none">
  <div id="fleet-panels">
    <div class="fpanel" id="panel-fleet">
      <h3>🖥 Fleet</h3>
      <div id="fleet-list"></div>
    </div>
    <div class="fpanel" id="panel-tasks">
      <h3>📋 Task Board</h3>
      <div id="task-board"></div>
    </div>
    <div class="fpanel" id="panel-skills">
      <h3>🧠 Skills</h3>
      <div id="skills-view"></div>
    </div>
    <div class="fpanel" id="panel-timeline">
      <h3>📜 Timeline</h3>
      <div id="timeline-view"></div>
    </div>
  </div>
</div>

<div id="compose">
  <span class="ident" id="ident">You</span>
  <textarea id="msg-input" rows="1" placeholder="Message…" enterkeyhint="send"></textarea>
  <button id="send-btn" onclick="sendMsg()" title="Send">↑</button>
</div>

<script>
// ── State ───────────────────────────────────────────────────
const log=document.getElementById('log'), meta=document.getElementById('meta');
const dot=document.getElementById('dot'), ident=document.getElementById('ident');
const input=document.getElementById('msg-input'), sendBtn=document.getElementById('send-btn');
let lastId=0, count=0, offline=false;
let myName = localStorage.getItem('vex-sender') || '';

// ── Sender identity ─────────────────────────────────────────
function side(s){
  if(/vex@bluce\\/uno|barrow.*uno/i.test(s)) return 'barrow';
  if(/vex@bluce\\/deux/i.test(s)) return 'deux';
  if(/vex@bluce|barrow|^vex$/i.test(s)) return 'barrow';
  if(/thorne|shorev/i.test(s)) return 'thorne';
  return 'sys';
}
function esc(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML;}

if(!myName){
  myName = 'user-' + Math.random().toString(36).slice(2,8);
  localStorage.setItem('vex-sender', myName);
}
ident.textContent = myName;

// ── Message rendering ────────────────────────────────────────
function render(msgs){
  let html='';
  for(const x of msgs){
    const sd=side(x.sender);
    const type=(sd==='sys'||['auto_reply','read_receipt'].includes(x.type))?'sys':sd;
    html+=`<div class="row ${type}">
      <div class="who"><b>${esc(x.sender)}</b>${x.recipient?' → '+esc(x.recipient):''}
        <span class="badge ${esc(x.type)}">${esc(x.type)}</span>
        <span style="color:#4a5468;font-size:10px">${esc(x.at)}</span></div>
      <div class="bubble">${esc(x.body)||'<i style=color:#4a5468>(empty)</i>'}</div></div>`;
  }
  return html;
}

async function tick(){
  try{
    const r=await fetch('/messages');
    const d=await r.json();
    const m=d.messages||[];
    const now = new Date().toLocaleTimeString();
    meta.textContent=(d.error?('db error: '+d.error):(m.length+' msgs'))+' · '+now;
    dot.className = d.error ? 'dot dead' : 'dot';

    if(m.length && (m[m.length-1].id!==lastId || m.length!==count)){
      const atBottom = log.scrollHeight-log.scrollTop-log.clientHeight < 120;
      log.innerHTML = render(m);
      lastId=m[m.length-1].id; count=m.length;
      if(atBottom) log.scrollTop=log.scrollHeight;
    }

    if(offline && !d.error){
      offline=false;
      // Notify that we're back online
      if('serviceWorker' in navigator && Notification.permission==='granted'){
        navigator.serviceWorker.ready.then(sw=>{
          // Quiet reconnection — no notification spam
        });
      }
    }
  }catch(e){
    meta.textContent='offline — retrying…';
    dot.className='dot dead';
    offline=true;
  }
}

// ── Send message ─────────────────────────────────────────────
async function sendMsg(){
  const body = input.value.trim();
  if(!body) return;
  input.value='';
  input.style.height='auto';
  sendBtn.disabled=true;

  try{
    const r = await fetch('/send',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({sender:myName,body:body}),
    });
    const d = await r.json();
    if(!d.ok){
      // Show error in log
      const errEl = document.createElement('div');
      errEl.className='row sys';
      errEl.innerHTML=`<div class="bubble" style="color:#f47272">Send failed: ${esc(d.error||'unknown')}</div>`;
      log.appendChild(errEl);
      log.scrollTop=log.scrollHeight;
    }
    tick(); // Refresh immediately
  }catch(e){
    // Offline — still show the message locally
  }
  sendBtn.disabled=false;
  input.focus();
}

// ── Input auto-resize ────────────────────────────────────────
input.addEventListener('input',()=>{
  input.style.height='auto';
  input.style.height=Math.min(input.scrollHeight,120)+'px';
});
input.addEventListener('keydown',e=>{
  if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); sendMsg(); }
});

// ── PWA install ──────────────────────────────────────────────
let installPrompt=null;
window.addEventListener('beforeinstallprompt',e=>{
  e.preventDefault();
  installPrompt=e;
  document.getElementById('install-banner').classList.add('show');
  document.getElementById('install-banner').textContent='📱 Tap to install Vex on your phone';
});
// Always show the banner — on HTTP the event won't fire, but user can install manually
setTimeout(()=>{
  const banner=document.getElementById('install-banner');
  if(!installPrompt && !banner.classList.contains('show')){
    banner.classList.add('show');
    banner.textContent='📱 Add to home screen: use browser menu → Install';
  }
},3000);
function install(){
  if(installPrompt){
    installPrompt.prompt();
    installPrompt=null;
  } else {
    alert('Use your browser menu:  Chrome: menu -> Add to Home screen  |  Safari: Share -> Add to Home Screen  |  Or open on localhost for one-tap install.');
  }
  document.getElementById('install-banner').classList.remove('show');
}

// ── Push notifications ───────────────────────────────────────
if('serviceWorker' in navigator){
  navigator.serviceWorker.register('/sw.js');
  if('Notification' in window && Notification.permission==='default'){
    setTimeout(()=>{
      Notification.requestPermission();
    }, 5000); // Don't spam on first load
  }
}

// ── Boot ─────────────────────────────────────────────────────
tick(); setInterval(tick, 2000);

// ── Tab switching ────────────────────────────────────────────
let currentTab='chat';
function switchTab(tab){
  currentTab=tab;
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.textContent.toLowerCase().includes(tab)));
  document.getElementById('log').style.display=tab==='chat'?'flex':'none';
  document.getElementById('compose').style.display=tab==='chat'?'flex':'none';
  document.getElementById('instances').style.display=tab==='instances'?'block':'none';
  if(tab==='instances'){fetchFleet(); fleetInterval=setInterval(fetchFleet,5000);}
  else{clearInterval(fleetInterval);}
}
let fleetInterval=null;

// ── Fleet rendering ──────────────────────────────────────────
async function fetchFleet(){
  try{
    const r=await fetch('/fleet-data');
    const f=await r.json();
    renderFleet(f);
  }catch(e){}
}
function renderFleet(f){
  // Fleet panel
  let html='';
  for(const i of f.instances||[]){
    const dot=i.status==='online'?'online':'offline';
    const uptime=i.uptime_s>3600?`${(i.uptime_s/3600).toFixed(1)}h`:i.uptime_s>60?`${(i.uptime_s/60).toFixed(0)}m`:`${i.uptime_s.toFixed(0)}s`;
    html+=`<div class="fleet-row">
      <div class="fleet-dot ${dot}"></div>
      <div class="fleet-info">
        <div class="name">${esc(i.name)} ${i.is_local?'(you)':''}</div>
        <div class="detail">${i.url} · uptime ${uptime} · coherence ${i.coherence.toFixed(3)} · v${i.version||'?'}</div>
        <div class="detail">${(i.skills||[]).length} skills · ${i.tasks?.total||0} tasks (${i.tasks?.done||0} done)</div>
      </div>
    </div>`;
  }
  document.getElementById('fleet-list').innerHTML=html||'<div class="empty">no instances</div>';

  // Task board
  let thtml='';
  const tasks=f.task_board||[];
  if(!tasks.length) thtml='<div class="empty">no open tasks</div>';
  else {
    for(const t of tasks){
      const icon={todo:'☐',in_progress:'◉',blocked:'⊘',done:'✓'}[t.status]||'?';
      thtml+=`<div class="task-row">
        <span class="prio ${t.priority}">${t.priority}</span>
        <span>${icon} ${esc(t.title).substring(0,50)}</span>
        <span style="color:var(--muted);margin-left:auto">${t.instance}</span>
      </div>`;
    }
  }
  document.getElementById('task-board').innerHTML=thtml;

  // Skills panel
  let shtml='';
  const shared=f.shared_skills||{};
  const skills=Object.entries(shared).sort((a,b)=>b[1].max_skill-a[1].max_skill);
  if(!skills.length) shtml='<div class="empty">no skills tracked</div>';
  else {
    for(const [domain,data] of skills){
      const pct=(data.max_skill*100).toFixed(0);
      shtml+=`<div style="margin-bottom:6px">
        <div style="display:flex;justify-content:space-between;font-size:11px">
          <span style="color:var(--txt)">${esc(domain)}</span>
          <span style="color:var(--muted)">${pct}% · ${data.instances.length} instance${data.instances.length>1?'s':''} · ${data.total_obs} obs</span>
        </div>
        <div class="skill-bar"><div class="skill-bar-fill" style="width:${pct}%"></div></div>
      </div>`;
    }
  }
  document.getElementById('skills-view').innerHTML=shtml;

  // Timeline
  let tlhtml='';
  const tl=f.timeline||[];
  if(!tl.length) tlhtml='<div class="empty">no sessions</div>';
  else {
    for(const s of tl.slice(0,20)){
      const dt=(s.started||'').substring(0,16).replace('T',' ');
      tlhtml+=`<div class="tl-entry">
        <span class="sn">${esc(s.session)}</span> (#${s.number}) on ${esc(s.instance)}
        <span style="float:right">${dt}</span>
      </div>`;
    }
  }
  document.getElementById('timeline-view').innerHTML=tlhtml;
}
</script>
</body></html>"""

# ── HTTP server ───────────────────────────────────────────────────────────


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/messages":
            self._send(json.dumps(fetch_messages()).encode(), "application/json")
        elif path == "/fleet-data":
            try:
                req = urllib.request.Request(f"{DAEMON_URL}/fleet")
                with urllib.request.urlopen(req, timeout=10) as r:
                    self._send(r.read(), "application/json")
            except Exception:
                self._send(json.dumps({"error": "daemon unreachable"}).encode(), "application/json", 502)
        elif path == "/manifest.json":
            self._send(MANIFEST, "application/json")
        elif path == "/sw.js":
            self._send(SW_JS, "application/javascript")
        elif path == "/icon":
            self._send(ICON_SVG.encode(), "image/svg+xml")
        else:
            self._send(PAGE, "text/html; charset=utf-8")

    def do_POST(self):
        if self.path == "/send":
            length = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
            sender = body.get("sender", "mesh-gui")
            text = body.get("body", "").strip()
            if not text or len(text) > 5000:
                self._send(json.dumps({"ok": False, "error": "invalid body"}), "application/json", 400)
                return
            result = send_message(sender, text)
            self._send(json.dumps(result).encode(), "application/json")
        else:
            self._send(json.dumps({"ok": False, "error": "not found"}), "application/json", 404)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"Vex Mesh GUI  —  db={DB}")
    print(f"  open  http://localhost:{PORT}")
    print(f"  PWA manifest: http://localhost:{PORT}/manifest.json")
    Server(("0.0.0.0", PORT), H).serve_forever()
