"""Dashboard web — lecture seule.

Expose :
  GET /           → HTML du dashboard
  GET /api/events → SSE : snapshots de signaux toutes les 2s
  GET /api/replay → données historiques depuis SQLite (replay post-session)
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..core.logging import logger

if TYPE_CHECKING:
    from ..core.events import TwitchClip


class SignalBroadcaster:
    """Fan-out de snapshots vers les clients SSE connectés."""

    def __init__(self, channel: str = "") -> None:
        self.channel = channel
        self._clients: list[asyncio.Queue] = []
        self._history: deque[dict] = deque(maxlen=300)  # ~10min à 2s/tick
        self._last_snap: dict = {}

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=300)
        for snap in self._history:
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                break
        self._clients.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._clients.remove(q)
        except ValueError:
            pass

    async def emit(self, data: dict) -> None:
        self._last_snap = data
        self._history.append(data)
        dead = []
        for q in self._clients:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    async def emit_clip(self, clip: TwitchClip) -> None:
        snap = {**self._last_snap, "clip": {"url": clip.url, "title": clip.title}}
        await self.emit(snap)


def create_app(broadcaster: SignalBroadcaster, db_path: str = "") -> FastAPI:
    app = FastAPI(title="Twitch Harvest Dashboard", docs_url=None, redoc_url=None)

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(_HTML.replace("__CHANNEL__", broadcaster.channel))

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        async def generate():
            q = broadcaster.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=5.0)
                        yield f"data: {json.dumps(data)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                broadcaster.unsubscribe(q)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/replay")
    async def replay() -> JSONResponse:
        """Charge la dernière session depuis SQLite pour replay post-session."""
        if not db_path:
            return JSONResponse([])
        try:
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """SELECT twitch_id, title, url, creator_name, view_count,
                              duration, clip_created_at, harvested_at
                       FROM twitch_clips ORDER BY harvested_at DESC LIMIT 50"""
                )
                rows = await cursor.fetchall()
                return JSONResponse([dict(r) for r in rows])
        except Exception as exc:
            logger.warning(f"[dashboard] replay: {exc}")
            return JSONResponse([])

    return app


# ---------------------------------------------------------------------------
# HTML — embarqué directement pour éviter les fichiers statiques
# ---------------------------------------------------------------------------

_HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Harvest · __CHANNEL__</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0e0e10;--s:#18181b;--b:#2d2d35;--t:#efeff1;--m:#adadb8;
      --p:#9147ff;--g:#00c267;--r:#eb4034;--gold:#f0a500;--teal:#00c2c2;
      --salmon:#ff6b6b;--lime:#a8ff78;--red:#ff4444}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-family:Inter,system-ui,sans-serif;
     display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* ── Header ── */
header{background:var(--s);border-bottom:1px solid var(--b);
       padding:10px 20px;display:flex;align-items:center;gap:16px;flex-shrink:0}
.ch{font-weight:700;font-size:16px;color:var(--p)}
.badge{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--m)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--r)}
.dot.on{background:var(--g);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.stats{margin-left:auto;display:flex;gap:20px}
.stat{text-align:center}
.stat b{display:block;font-size:18px;font-weight:700}
.stat small{font-size:10px;color:var(--m);text-transform:uppercase;letter-spacing:.5px}

/* ── Layout principal ── */
.main{flex:1;display:grid;grid-template-columns:1fr 1fr;
      grid-template-rows:1fr 1fr;gap:1px;background:var(--b);min-height:0;overflow:hidden}
.panel{background:var(--s);padding:14px;display:flex;flex-direction:column;min-height:0}
.ptitle{font-size:10px;font-weight:600;color:var(--m);text-transform:uppercase;
        letter-spacing:.8px;margin-bottom:8px;flex-shrink:0}
.cwrap{flex:1;position:relative;min-height:0}

/* ── Légende ── */
.legend{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px;flex-shrink:0}
.leg{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--m)}
.ld{width:8px;height:8px;border-radius:50%}

/* ── Chat panel ── */
.chat-panel{background:var(--s);padding:10px 14px;display:flex;flex-direction:column;
            min-height:0;overflow:hidden}
.chat-msgs{flex:1;overflow-y:auto;display:flex;flex-direction:column-reverse;gap:3px}
.chat-msgs::-webkit-scrollbar{width:3px}
.chat-msgs::-webkit-scrollbar-thumb{background:var(--b);border-radius:2px}
.msg{font-size:11px;line-height:1.4;word-break:break-word}
.msg .au{color:var(--p);font-weight:600;margin-right:4px}
.msg .ct{color:var(--t)}
.msg.spike .au{color:var(--gold)}

/* ── Bottom bar : clips ── */
.clips-bar{background:var(--s);border-top:1px solid var(--b);
           padding:8px 20px;flex-shrink:0;max-height:120px;overflow-y:auto}
.clips-bar::-webkit-scrollbar{width:3px}
.clips-bar::-webkit-scrollbar-thumb{background:var(--b);border-radius:2px}
.ctitle{font-size:10px;font-weight:600;color:var(--m);text-transform:uppercase;
        letter-spacing:.8px;margin-bottom:6px}
.clist{display:flex;flex-direction:column;gap:4px}
.crow{display:flex;align-items:center;gap:10px;font-size:12px;
      border-bottom:1px solid var(--b);padding-bottom:4px}
.crow:last-child{border:none;padding:0}
.ctime{color:var(--m);font-variant-numeric:tabular-nums;min-width:58px}
.cscore{color:var(--p);font-weight:700;min-width:32px}
.ctitle2{color:var(--m);flex:1;font-size:11px;overflow:hidden;
          text-overflow:ellipsis;white-space:nowrap}
.curl{color:var(--p);text-decoration:none}
.curl:hover{text-decoration:underline}
.empty{color:var(--m);font-size:12px;font-style:italic}

/* ── Replay modal ── */
.fab{position:fixed;bottom:20px;right:20px;background:var(--p);color:#fff;
     border:none;border-radius:8px;padding:8px 16px;font-size:13px;
     font-weight:600;cursor:pointer;z-index:10}
.fab:hover{background:#7d2ff7}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
       z-index:20;align-items:center;justify-content:center}
.modal.open{display:flex}
.mbox{background:var(--s);border:1px solid var(--b);border-radius:12px;
      padding:24px;width:600px;max-height:70vh;display:flex;flex-direction:column;gap:12px}
.mbox h2{font-size:14px;font-weight:700}
.mbox .close{margin-left:auto;background:none;border:none;color:var(--m);
             font-size:18px;cursor:pointer;line-height:1}
.mbox .close:hover{color:var(--t)}
.mhead{display:flex;align-items:center}
.rtable{overflow-y:auto;flex:1}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;color:var(--m);font-weight:600;padding:4px 8px;
   border-bottom:1px solid var(--b);font-size:10px;text-transform:uppercase}
td{padding:6px 8px;border-bottom:1px solid var(--b);color:var(--t)}
tr:last-child td{border:none}
td a{color:var(--p);text-decoration:none}
td a:hover{text-decoration:underline}
</style>
</head>
<body>

<header>
  <span class="ch">#__CHANNEL__</span>
  <span class="badge"><span class="dot" id="dot"></span><span id="st">Connexion…</span></span>
  <div class="stats">
    <div class="stat"><b id="sMsg">0</b><small>messages</small></div>
    <div class="stat"><b id="sClip">0</b><small>clips</small></div>
    <div class="stat"><b id="sVel">0.00</b><small>msg/s</small></div>
    <div class="stat"><b id="sBase">0.00</b><small>baseline</small></div>
  </div>
</header>

<div class="main">

  <!-- Velocity -->
  <div class="panel">
    <div class="ptitle">Velocity — msg/s vs baseline</div>
    <div class="cwrap"><canvas id="cVel"></canvas></div>
  </div>

  <!-- Scores -->
  <div class="panel">
    <div class="ptitle">Signaux & composite (0 → 100)</div>
    <div class="legend">
      <span class="leg"><span class="ld" style="background:var(--p)"></span>Velocity</span>
      <span class="leg"><span class="ld" style="background:var(--gold)"></span>Emote</span>
      <span class="leg"><span class="ld" style="background:var(--teal)"></span>Chatters</span>
      <span class="leg"><span class="ld" style="background:var(--salmon)"></span>Caps</span>
      <span class="leg"><span class="ld" style="background:var(--lime)"></span>Copypasta</span>
      <span class="leg"><span class="ld" style="background:#fff;border:1px solid var(--b)"></span>Composite</span>
      <span class="leg"><span class="ld" style="background:var(--red);border-radius:2px"></span>Clip</span>
    </div>
    <div class="cwrap"><canvas id="cSig"></canvas></div>
  </div>

  <!-- Chat live -->
  <div class="chat-panel">
    <div class="ptitle">Chat live</div>
    <div class="chat-msgs" id="chatMsgs">
      <span class="empty">En attente de messages…</span>
    </div>
  </div>

  <!-- Placeholder pour symétrie (peut servir plus tard) -->
  <div class="panel" style="justify-content:center;align-items:center">
    <span style="color:var(--m);font-size:12px;text-align:center">
      Prochaine feature ici<br>
      <span style="font-size:10px">(replay · heatmap · tuning)</span>
    </span>
  </div>

</div>

<!-- Clips bar -->
<div class="clips-bar">
  <div class="ctitle">Clips créés cette session</div>
  <div class="clist" id="clist"><span class="empty">Aucun clip encore…</span></div>
</div>

<!-- Replay button -->
<button class="fab" onclick="openReplay()">📊 Replay session</button>

<!-- Replay modal -->
<div class="modal" id="modal" onclick="if(event.target===this)closeReplay()">
  <div class="mbox">
    <div class="mhead">
      <h2>Historique des clips (SQLite)</h2>
      <button class="close" onclick="closeReplay()">✕</button>
    </div>
    <div class="rtable" id="rtable"><span class="empty">Chargement…</span></div>
  </div>
</div>

<script>
// ── Constantes ──────────────────────────────────────────────
const N = 150;  // 5min @ 2s/tick
const CHAT_MAX = 40;

// ── État ────────────────────────────────────────────────────
const times=[], vArr=[], bArr=[];
const sv=[], se=[], su=[], sc=[], sr=[], scomp=[];
const clipEvents=[];  // {absIdx, url, title}
let totalPts=0, clipCount=0, chatMsgCount=0;

// ── Plugin lignes verticales (clips) ────────────────────────
const clipLinePlugin={
  id:'clipLines',
  afterDraw(chart){
    if(!clipEvents.length) return;
    const{ctx,scales:{x,y}}=chart;
    const offset=totalPts-times.length;
    ctx.save();
    clipEvents.forEach(({absIdx})=>{
      const rel=absIdx-offset;
      if(rel<0||rel>=times.length) return;
      const px=x.getPixelForValue(rel);
      ctx.beginPath();ctx.moveTo(px,y.top);ctx.lineTo(px,y.bottom);
      ctx.strokeStyle='#ff4444';ctx.lineWidth=2;ctx.setLineDash([4,4]);ctx.stroke();
    });
    ctx.restore();
  }
};
Chart.register(clipLinePlugin);
Chart.defaults.color='#adadb8';
Chart.defaults.borderColor='#2d2d35';
Chart.defaults.font={family:'Inter,system-ui,sans-serif',size:11};

// ── Helpers Chart.js ─────────────────────────────────────────
const baseOpts=(yMax)=>({
  responsive:true,maintainAspectRatio:false,animation:false,
  interaction:{mode:'index',intersect:false},
  plugins:{legend:{display:false},tooltip:{
    backgroundColor:'#18181b',borderColor:'#2d2d35',borderWidth:1,
    titleColor:'#efeff1',bodyColor:'#adadb8'
  }},
  scales:{
    x:{type:'linear',min:0,
       ticks:{maxTicksLimit:6,callback:v=>times[Math.round(v)]||''},
       grid:{color:'#2d2d35'}},
    y:{min:0,...(yMax?{max:yMax}:{}),grid:{color:'#2d2d35'},ticks:{maxTicksLimit:5}}
  }
});

// ── Graphique Velocity ────────────────────────────────────────
const velChart=new Chart(document.getElementById('cVel'),{
  type:'line',
  data:{labels:[],datasets:[
    {label:'msg/s',data:vArr,borderColor:'#9147ff',
     backgroundColor:'rgba(145,71,255,.12)',fill:true,tension:.3,pointRadius:0,borderWidth:2},
    {label:'baseline',data:bArr,borderColor:'#adadb8',borderDash:[5,5],
     fill:false,tension:.3,pointRadius:0,borderWidth:1.5}
  ]},
  options:baseOpts(null)
});

// ── Graphique Signaux ─────────────────────────────────────────
const sigChart=new Chart(document.getElementById('cSig'),{
  type:'line',
  data:{labels:[],datasets:[
    {label:'Velocity', data:sv,borderColor:'#9147ff',fill:false,tension:.3,pointRadius:0,borderWidth:2},
    {label:'Emote',    data:se,borderColor:'#f0a500',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Chatters', data:su,borderColor:'#00c2c2',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Caps',     data:sc,borderColor:'#ff6b6b',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Copypasta',data:sr,borderColor:'#a8ff78',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Composite',data:scomp,borderColor:'#ffffff',fill:false,tension:.3,
     pointRadius:0,borderWidth:2.5,borderDash:[3,2]},
  ]},
  options:baseOpts(100)
});

// ── Push snapshot ─────────────────────────────────────────────
function push(snap){
  const absIdx=totalPts++;
  times.push(snap.t||'');
  vArr.push(snap.v_val??0); bArr.push(snap.v_base??0);
  sv.push(snap.v_score??0); se.push(snap.e_score??0);
  su.push(snap.u_score??0); sc.push(snap.c_score??0); sr.push(snap.r_score??0);
  scomp.push(snap.composite??0);

  if(snap.clip){
    clipEvents.push({absIdx,url:snap.clip.url,title:snap.clip.title});
    clipCount++; document.getElementById('sClip').textContent=clipCount;
    addClip(snap.clip,snap.t,snap.composite??snap.v_score??0);
  }

  // Trim fenêtre glissante
  if(times.length>N){
    times.shift();vArr.shift();bArr.shift();
    sv.shift();se.shift();su.shift();sc.shift();sr.shift();scomp.shift();
  }

  // Chat messages
  if(snap.chat_msgs?.length) snap.chat_msgs.forEach(m=>addChat(m,snap.v_score>0));

  // Mise à jour charts
  const xi=[...Array(times.length).keys()];
  velChart.data.labels=xi;
  velChart.data.datasets[0].data=[...vArr];
  velChart.data.datasets[1].data=[...bArr];
  velChart.update('none');

  sigChart.data.labels=xi;
  [sv,se,su,sc,sr,scomp].forEach((d,i)=>sigChart.data.datasets[i].data=[...d]);
  sigChart.update('none');

  document.getElementById('sMsg').textContent=(snap.msgs??0).toLocaleString('fr-FR');
  document.getElementById('sVel').textContent=(snap.v_val??0).toFixed(2);
  document.getElementById('sBase').textContent=(snap.v_base??0).toFixed(2);
}

// ── Chat live ─────────────────────────────────────────────────
function addChat(msg,isSpike){
  const box=document.getElementById('chatMsgs');
  box.querySelector('.empty')?.remove();
  const d=document.createElement('div');
  d.className='msg'+(isSpike?' spike':'');
  d.innerHTML=`<span class="au">${esc(msg.a)}</span><span class="ct">${esc(msg.c)}</span>`;
  box.insertBefore(d,box.firstChild);
  chatMsgCount++;
  // Trim
  while(box.children.length>CHAT_MAX) box.removeChild(box.lastChild);
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

// ── Clips bar ─────────────────────────────────────────────────
function addClip(clip,t,score){
  const list=document.getElementById('clist');
  list.querySelector('.empty')?.remove();
  const d=document.createElement('div');d.className='crow';
  d.innerHTML=`<span class="ctime">${t}</span><span class="cscore">${(+score).toFixed(0)}</span>`+
    `<span class="ctitle2">${esc(clip.title||'clip créé')}</span>`+
    `<a class="curl" href="${clip.url}" target="_blank">Voir →</a>`;
  list.insertBefore(d,list.firstChild);
}

// ── Replay modal ──────────────────────────────────────────────
async function openReplay(){
  document.getElementById('modal').classList.add('open');
  document.getElementById('rtable').innerHTML='<span class="empty">Chargement…</span>';
  try{
    const r=await fetch('/api/replay');
    const rows=await r.json();
    if(!rows.length){
      document.getElementById('rtable').innerHTML='<span class="empty">Aucun clip en base.</span>';
      return;
    }
    const t=document.createElement('table');
    t.innerHTML=`<tr><th>Heure</th><th>Titre</th><th>Créateur</th><th>Durée</th><th>Vues</th><th>Lien</th></tr>`;
    rows.forEach(r=>{
      const tr=document.createElement('tr');
      const dt=new Date(r.harvested_at).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'});
      tr.innerHTML=`<td>${dt}</td><td>${esc(r.title)}</td><td>${esc(r.creator_name)}</td>`+
        `<td>${r.duration}s</td><td>${r.view_count}</td>`+
        `<td><a href="${r.url}" target="_blank">Voir</a></td>`;
      t.appendChild(tr);
    });
    document.getElementById('rtable').innerHTML='';
    document.getElementById('rtable').appendChild(t);
  }catch(e){
    document.getElementById('rtable').innerHTML='<span class="empty">Erreur de chargement.</span>';
  }
}
function closeReplay(){document.getElementById('modal').classList.remove('open')}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeReplay()});

// ── SSE ───────────────────────────────────────────────────────
function connect(){
  const es=new EventSource('/api/events');
  es.onopen=()=>{
    document.getElementById('dot').classList.add('on');
    document.getElementById('st').textContent='En écoute';
  };
  es.onmessage=e=>push(JSON.parse(e.data));
  es.onerror=()=>{
    document.getElementById('dot').classList.remove('on');
    document.getElementById('st').textContent='Reconnexion…';
    es.close();setTimeout(connect,3000);
  };
}
connect();
</script>
</body>
</html>"""
