"""Dashboard web — lecture seule.

Expose :
  GET /           → HTML du dashboard live
  GET /sessions   → HTML historique des sessions
  GET /api/events → SSE : snapshots de signaux toutes les 2s
  GET /api/replay → données historiques depuis SQLite (sessions + clips)
"""
from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..core.db import Database
from ..core.logging import logger

if TYPE_CHECKING:
    from ..core.events import TwitchClip


class SignalBroadcaster:
    """Fan-out de snapshots vers les clients SSE connectés."""

    def __init__(self, channel: str = "") -> None:
        self.channel = channel
        self._clients: list[asyncio.Queue] = []
        self._history: deque[dict] = deque(maxlen=300)
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
        snap = {
            **self._last_snap,
            "clip": {
                "url": clip.url,
                "title": clip.title,
                "thumbnail_url": clip.thumbnail_url or "",
                "composite_score": clip.composite_score,
                "v_score": clip.v_score,
                "e_score": clip.e_score,
                "u_score": clip.u_score,
                "c_score": clip.c_score,
                "r_score": clip.r_score,
                "duration": clip.duration,
            },
        }
        await self.emit(snap)


def create_app(broadcaster: SignalBroadcaster, db_path: str = "") -> FastAPI:
    app = FastAPI(title="Twitch Harvest Dashboard", docs_url=None, redoc_url=None)
    db: Database | None = Database(Path(db_path)) if db_path else None

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse(_HTML.replace("__CHANNEL__", broadcaster.channel))

    @app.get("/sessions")
    async def sessions_page() -> HTMLResponse:
        return HTMLResponse(_HTML_SESSIONS)

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
        if not db:
            return JSONResponse([])
        try:
            sessions = await db.get_all_sessions()
            result = []
            for s in sessions:
                clips = await db.get_clips_by_session(s["id"])
                result.append({**s, "clips": clips})
            return JSONResponse(result)
        except Exception as exc:
            logger.warning(f"[dashboard] replay: {exc}")
            return JSONResponse([])

    return app


# ---------------------------------------------------------------------------
# HTML live dashboard
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
.nav-link{color:var(--m);text-decoration:none;font-size:12px;padding:4px 10px;
          border:1px solid var(--b);border-radius:6px;white-space:nowrap}
.nav-link:hover{color:var(--t);border-color:var(--m)}
.hstats{margin-left:auto;display:flex;gap:20px}
.stat{text-align:center}
.stat b{display:block;font-size:18px;font-weight:700}
.stat small{font-size:10px;color:var(--m);text-transform:uppercase;letter-spacing:.5px}
.z-idle{color:var(--t)}
.z-warm{color:var(--gold)}
.z-hot{color:var(--red)}

/* ── Main 2x2 ── */
.main{flex:1;display:grid;grid-template-columns:1fr 1fr;
      grid-template-rows:1fr 1fr;gap:1px;background:var(--b);min-height:0;overflow:hidden}
.panel{background:var(--s);padding:14px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
.ptitle{font-size:10px;font-weight:600;color:var(--m);text-transform:uppercase;
        letter-spacing:.8px;margin-bottom:8px;flex-shrink:0}
.cwrap{flex:1;position:relative;min-height:0}

/* ── Legend ── */
.legend{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px;flex-shrink:0}
.leg{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--m)}
.ld{width:8px;height:8px;border-radius:50%}

/* ── Stats session panel ── */
.kv{display:grid;grid-template-columns:auto 1fr;gap:3px 14px;flex-shrink:0}
.kv-l{color:var(--m);font-size:11px;text-align:right;line-height:1.8}
.kv-v{font-size:12px;font-weight:600;line-height:1.8}
.sep{height:1px;background:var(--b);margin:10px 0;flex-shrink:0}
.bar-row{display:flex;align-items:center;gap:8px;font-size:11px;flex-shrink:0;margin-top:6px}
.bar-label{color:var(--m);width:60px;flex-shrink:0}
.bar-track{flex:1;height:5px;background:var(--b);border-radius:3px;overflow:hidden}
.bar-fill{height:100%;border-radius:3px}
.bar-wu{background:var(--g);transition:width .5s}
.bar-cd{transition:width 1s linear}
.bar-val{width:40px;text-align:right;color:var(--m);font-variant-numeric:tabular-nums}

/* ── Clips table panel ── */
.tbl-wrap{flex:1;overflow-y:auto;min-height:0}
.tbl-wrap::-webkit-scrollbar{width:3px}
.tbl-wrap::-webkit-scrollbar-thumb{background:var(--b);border-radius:2px}
.ctbl{width:100%;border-collapse:collapse;font-size:11px;table-layout:fixed}
.ctbl colgroup col.c-time{width:52px}
.ctbl colgroup col.c-title{width:auto}
.ctbl colgroup col.c-score{width:40px}
.ctbl colgroup col.c-sig{width:32px}
.ctbl colgroup col.c-dur{width:34px}
.ctbl colgroup col.c-link{width:20px}
.ctbl th{color:var(--m);font-weight:600;padding:0 4px 5px;border-bottom:1px solid var(--b);
         font-size:10px;text-transform:uppercase;cursor:pointer;white-space:nowrap;
         text-align:right;user-select:none}
.ctbl th:nth-child(1),.ctbl th:nth-child(2){text-align:left}
.ctbl th.sort-asc::after{content:' \25b2';font-size:8px}
.ctbl th.sort-desc::after{content:' \25bc';font-size:8px}
.ctbl td{padding:4px 4px;border-bottom:1px solid var(--b);text-align:right;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctbl td:nth-child(1){text-align:left;color:var(--m)}
.ctbl td:nth-child(2){text-align:left}
.ctbl tr:last-child td{border:none}
.ctbl .dim{color:var(--m)!important;font-weight:normal!important}
.ctbl td a{color:var(--m);text-decoration:none}
.ctbl td a:hover{color:var(--p)}
.cbadge{background:var(--p);color:#fff;border-radius:10px;padding:1px 7px;
        font-size:10px;font-weight:700;margin-left:6px;vertical-align:middle}
.empty{color:var(--m);font-size:12px;font-style:italic}
</style>
</head>
<body>

<header>
  <span class="ch">#__CHANNEL__</span>
  <a href="/sessions" class="nav-link">&#128202; Historique</a>
  <span class="badge"><span class="dot" id="dot"></span><span id="st">Connexion&#8230;</span></span>
  <div class="hstats">
    <div class="stat"><b id="sMsg">0</b><small>messages</small></div>
    <div class="stat"><b id="sClip">0</b><small>clips</small></div>
    <div class="stat"><b id="sVel">0.00</b><small>msg/s</small></div>
    <div class="stat"><b id="sBase">0.00</b><small>baseline</small></div>
    <div class="stat"><b id="sZ" class="z-idle">0.0</b><small>Z-score</small></div>
  </div>
</header>

<div class="main">

  <!-- Velocity -->
  <div class="panel">
    <div class="ptitle">Velocity &#8212; msg/s vs baseline</div>
    <div class="cwrap"><canvas id="cVel"></canvas></div>
  </div>

  <!-- Signals -->
  <div class="panel">
    <div class="ptitle">Signaux &amp; composite (0 &#8594; 100)</div>
    <div class="legend">
      <span class="leg"><span class="ld" style="background:var(--p)"></span>Velocity</span>
      <span class="leg"><span class="ld" style="background:var(--gold)"></span>Emote</span>
      <span class="leg"><span class="ld" style="background:var(--teal)"></span>Chatters</span>
      <span class="leg"><span class="ld" style="background:var(--salmon)"></span>Caps</span>
      <span class="leg"><span class="ld" style="background:var(--lime)"></span>Copypasta</span>
      <span class="leg"><span class="ld" style="background:#fff;border:1px solid var(--b)"></span>Composite</span>
    </div>
    <div class="cwrap"><canvas id="cSig"></canvas></div>
  </div>

  <!-- Stats session -->
  <div class="panel">
    <div class="ptitle">Session en cours</div>
    <div class="kv">
      <span class="kv-l">Dur&#233;e</span>      <span class="kv-v" id="sDur">&#8212;</span>
      <span class="kv-l">Messages</span>   <span class="kv-v" id="sMsgTot">0</span>
      <span class="kv-l">Msg/s moyen</span><span class="kv-v" id="sAvgVel">&#8212;</span>
      <span class="kv-l">Clips cr&#233;&#233;s</span><span class="kv-v" id="sClipCount">0</span>
      <span class="kv-l">Dernier clip</span><span class="kv-v" id="sLast">&#8212;</span>
    </div>
    <div class="sep"></div>
    <div class="bar-row" id="wuRow">
      <span class="bar-label">Warmup</span>
      <div class="bar-track"><div class="bar-fill bar-wu" id="wuFill" style="width:0%"></div></div>
      <span class="bar-val" id="wuVal">0/30</span>
    </div>
    <div class="bar-row">
      <span class="bar-label">Cooldown</span>
      <div class="bar-track"><div class="bar-fill bar-cd" id="cdFill" style="width:0%;background:var(--g)"></div></div>
      <span class="bar-val" id="cdVal">pr&#234;t</span>
    </div>
  </div>

  <!-- Clips table -->
  <div class="panel">
    <div class="ptitle">Clips cette session <span id="cbadge" class="cbadge" style="display:none">0</span></div>
    <div class="tbl-wrap">
      <span class="empty" id="noClips">Aucun clip encore&#8230;</span>
      <table class="ctbl" id="ctbl" style="display:none">
        <colgroup>
          <col class="c-time"><col class="c-title">
          <col class="c-score"><col class="c-sig"><col class="c-sig">
          <col class="c-sig"><col class="c-sig"><col class="c-sig">
          <col class="c-dur"><col class="c-link">
        </colgroup>
        <thead><tr>
          <th data-col="t">Heure</th>
          <th data-col="title">Titre</th>
          <th data-col="composite_score" class="sort-desc">Score</th>
          <th data-col="v_score" style="color:#9147ff">V</th>
          <th data-col="e_score" style="color:#f0a500">E</th>
          <th data-col="u_score" style="color:#00c2c2">U</th>
          <th data-col="c_score" style="color:#ff6b6b">C</th>
          <th data-col="r_score" style="color:#a8ff78">R</th>
          <th data-col="duration">Dur</th>
          <th></th>
        </tr></thead>
        <tbody id="ctblBody"></tbody>
      </table>
    </div>
  </div>

</div>

<script>
const N=150, COOLDOWN_MS=120_000;
const times=[],vArr=[],bArr=[];
const sv=[],se=[],su=[],sc=[],sr=[],scomp=[];
let totalPts=0;
const clipEvents=[];
const sessionStart=Date.now();
let velSum=0,velTicks=0,clipCount=0,lastClipMs=null;
const liveClips=[];
let sortCol='composite_score',sortDir=-1;

// Duration timer
setInterval(()=>{
  const e=Math.floor((Date.now()-sessionStart)/1000);
  const h=Math.floor(e/3600),m=Math.floor((e%3600)/60),s=e%60;
  document.getElementById('sDur').textContent=
    h?`${h}h${String(m).padStart(2,'0')}m`:`${m}m${String(s).padStart(2,'0')}s`;
},1000);

// Cooldown bar
setInterval(()=>{
  const fill=document.getElementById('cdFill');
  if(!lastClipMs){
    fill.style.width='0%';fill.style.background='var(--g)';
    document.getElementById('cdVal').textContent='prêt';return;
  }
  const rem=Math.max(0,COOLDOWN_MS-(Date.now()-lastClipMs));
  fill.style.width=(rem/COOLDOWN_MS*100)+'%';
  fill.style.background=rem>0?'var(--r)':'var(--g)';
  document.getElementById('cdVal').textContent=rem>0?Math.ceil(rem/1000)+'s':'prêt';
},500);

// Clip-line plugin (shared by both charts)
const clipLinePlugin={
  id:'clipLines',
  afterDraw(chart){
    if(!clipEvents.length)return;
    const{ctx,scales:{x,y}}=chart;
    const offset=totalPts-times.length;
    ctx.save();
    clipEvents.forEach(({absIdx})=>{
      const rel=absIdx-offset;
      if(rel<0||rel>=times.length)return;
      const px=x.getPixelForValue(rel);
      ctx.strokeStyle='rgba(255,68,68,.7)';ctx.lineWidth=1.5;ctx.setLineDash([4,3]);
      ctx.beginPath();ctx.moveTo(px,y.top);ctx.lineTo(px,y.bottom);ctx.stroke();
      ctx.setLineDash([]);ctx.fillStyle='#ff4444';
      ctx.beginPath();ctx.moveTo(px-4,y.top);ctx.lineTo(px+4,y.top);ctx.lineTo(px,y.top+7);
      ctx.closePath();ctx.fill();
    });
    ctx.restore();
  }
};
Chart.register(clipLinePlugin);
Chart.defaults.color='#adadb8';
Chart.defaults.borderColor='#2d2d35';
Chart.defaults.font={family:'Inter,system-ui,sans-serif',size:11};

const baseOpts=(yMax)=>({
  responsive:true,maintainAspectRatio:false,animation:false,
  interaction:{mode:'index',intersect:false},
  plugins:{
    legend:{display:false},
    tooltip:{backgroundColor:'#18181b',borderColor:'#2d2d35',borderWidth:1,
             titleColor:'#efeff1',bodyColor:'#adadb8'},
    clipLines:{}
  },
  scales:{
    x:{type:'linear',min:0,
       ticks:{maxTicksLimit:6,callback:v=>times[Math.round(v)]||''},
       grid:{color:'#2d2d35'}},
    y:{min:0,...(yMax?{max:yMax}:{}),grid:{color:'#2d2d35'},ticks:{maxTicksLimit:5}}
  }
});

const velChart=new Chart(document.getElementById('cVel'),{
  type:'line',data:{labels:[],datasets:[
    {label:'msg/s',data:vArr,borderColor:'#9147ff',backgroundColor:'rgba(145,71,255,.12)',
     fill:true,tension:.3,pointRadius:0,borderWidth:2},
    {label:'baseline',data:bArr,borderColor:'#adadb8',borderDash:[5,5],
     fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
  ]},options:baseOpts(null)
});

const sigChart=new Chart(document.getElementById('cSig'),{
  type:'line',data:{labels:[],datasets:[
    {label:'Velocity', data:sv,borderColor:'#9147ff',fill:false,tension:.3,pointRadius:0,borderWidth:2},
    {label:'Emote',    data:se,borderColor:'#f0a500',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Chatters', data:su,borderColor:'#00c2c2',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Caps',     data:sc,borderColor:'#ff6b6b',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Copypasta',data:sr,borderColor:'#a8ff78',fill:false,tension:.3,pointRadius:0,borderWidth:1.5},
    {label:'Composite',data:scomp,borderColor:'#fff',fill:false,tension:.3,
     pointRadius:0,borderWidth:2.5,borderDash:[3,2]},
  ]},options:baseOpts(100)
});

function push(snap){
  const absIdx=totalPts++;
  times.push(snap.t||'');
  vArr.push(snap.v_val??0);bArr.push(snap.v_base??0);
  sv.push(snap.v_score??0);se.push(snap.e_score??0);
  su.push(snap.u_score??0);sc.push(snap.c_score??0);sr.push(snap.r_score??0);
  scomp.push(snap.composite??0);

  if(snap.clip){
    clipEvents.push({absIdx});
    clipCount++;lastClipMs=Date.now();
    liveClips.push({...snap.clip,t:snap.t});
    flashClip();
    document.getElementById('sLast').textContent=
      `${snap.t} · ${Math.round(snap.clip.composite_score||0)}`;
    document.getElementById('sClipCount').textContent=clipCount;
    document.getElementById('sClip').textContent=clipCount;
    const b=document.getElementById('cbadge');b.style.display='inline';b.textContent=clipCount;
    renderLiveTable();
  }

  if(times.length>N){
    times.shift();vArr.shift();bArr.shift();
    sv.shift();se.shift();su.shift();sc.shift();sr.shift();scomp.shift();
  }

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
  document.getElementById('sMsgTot').textContent=(snap.msgs??0).toLocaleString('fr-FR');

  const z=snap.v_z??0;
  const zEl=document.getElementById('sZ');
  zEl.textContent=z.toFixed(1);
  zEl.className=z>=2.5?'z-hot':z>=1.5?'z-warm':'z-idle';

  velSum+=snap.v_val??0;velTicks++;
  document.getElementById('sAvgVel').textContent=(velSum/velTicks).toFixed(1);

  const samples=snap.v_samples??0,wmx=snap.warmup_max??30;
  const wuRow=document.getElementById('wuRow');
  if(samples<wmx){
    wuRow.style.display='';
    document.getElementById('wuFill').style.width=(samples/wmx*100)+'%';
    document.getElementById('wuVal').textContent=`${samples}/${wmx}`;
  }else{
    wuRow.style.display='none';
  }
}

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function sigCell(val,color){
  if(!val||+val<=0)return`<td class="dim">—</td>`;
  return`<td style="color:${color};font-weight:600">${Math.round(+val)}</td>`;
}

function renderLiveTable(){
  const data=[...liveClips].sort((a,b)=>sortDir*((+a[sortCol]||0)-(+b[sortCol]||0)));
  document.getElementById('noClips').style.display='none';
  document.getElementById('ctbl').style.display='table';
  document.getElementById('ctblBody').innerHTML=data.map(c=>`<tr>
    <td class="dim">${c.t}</td>
    <td title="${esc(c.title)}">${esc(c.title)}</td>
    <td style="font-weight:700">${Math.round(+c.composite_score||0)}</td>
    ${sigCell(c.v_score,'#9147ff')}
    ${sigCell(c.e_score,'#f0a500')}
    ${sigCell(c.u_score,'#00c2c2')}
    ${sigCell(c.c_score,'#ff6b6b')}
    ${sigCell(c.r_score,'#a8ff78')}
    <td class="dim">${(+c.duration||0).toFixed(0)}s</td>
    <td><a href="${c.url}" target="_blank">↗</a></td>
  </tr>`).join('');
  document.querySelectorAll('#ctbl th[data-col]').forEach(th=>{
    th.classList.remove('sort-asc','sort-desc');
    if(th.dataset.col===sortCol)th.classList.add(sortDir>0?'sort-asc':'sort-desc');
  });
}

document.querySelectorAll('#ctbl th[data-col]').forEach(th=>{
  th.addEventListener('click',()=>{
    sortCol===th.dataset.col?sortDir*=-1:(sortCol=th.dataset.col,sortDir=-1);
    renderLiveTable();
  });
});

function flashClip(){
  document.body.style.boxShadow='inset 0 0 0 3px #ff4444';
  requestAnimationFrame(()=>setTimeout(()=>{
    document.body.style.transition='box-shadow 1.5s ease-out';
    document.body.style.boxShadow='inset 0 0 0 3px transparent';
    setTimeout(()=>{document.body.style.transition='';document.body.style.boxShadow=''},1500);
  },80));
}

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


# ---------------------------------------------------------------------------
# HTML sessions history page
# ---------------------------------------------------------------------------

_HTML_SESSIONS = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Harvest · Historique</title>
<style>
:root{--bg:#0e0e10;--s:#18181b;--b:#2d2d35;--t:#efeff1;--m:#adadb8;
      --p:#9147ff;--g:#00c267;--r:#eb4034;--gold:#f0a500;--teal:#00c2c2;
      --salmon:#ff6b6b;--lime:#a8ff78}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t);font-family:Inter,system-ui,sans-serif;min-height:100vh}

/* ── Header ── */
header{background:var(--s);border-bottom:1px solid var(--b);
       padding:12px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:10}
.back{color:var(--m);text-decoration:none;font-size:12px;padding:4px 10px;
      border:1px solid var(--b);border-radius:6px;white-space:nowrap}
.back:hover{color:var(--t);border-color:var(--m)}
.htitle{font-weight:700;font-size:15px}
.hsub{color:var(--m);font-size:12px;margin-left:auto}

/* ── Content ── */
main{padding:20px 24px;max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:16px}

/* ── Session card ── */
.scard{background:var(--s);border:1px solid var(--b);border-radius:10px;overflow:hidden}
.scard-head{padding:12px 18px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;
            cursor:pointer;user-select:none;border-bottom:1px solid transparent}
.scard.open .scard-head{border-bottom-color:var(--b)}
.scard-head:hover{background:rgba(255,255,255,.03)}
.schan{font-weight:700;font-size:14px;color:var(--p);min-width:80px}
.smeta{color:var(--m);font-size:12px}
.sdur{color:var(--m);font-size:12px}
.scount{background:var(--b);border-radius:20px;padding:2px 10px;font-size:12px;font-weight:600}
.stop{color:var(--gold);font-size:12px;font-weight:700}
.stoggle{color:var(--m);font-size:14px;margin-left:auto;transition:transform .2s}
.scard.open .stoggle{transform:rotate(180deg)}

/* ── Table ── */
.scard-body{display:none;overflow-x:auto}
.scard.open .scard-body{display:block}
.ctbl{width:100%;border-collapse:collapse;font-size:12px;table-layout:fixed}
.ctbl colgroup col.c-time{width:54px}
.ctbl colgroup col.c-title{min-width:140px}
.ctbl colgroup col.c-score{width:48px}
.ctbl colgroup col.c-sig{width:36px}
.ctbl colgroup col.c-dur{width:38px}
.ctbl colgroup col.c-link{width:24px}
.ctbl thead tr{background:rgba(255,255,255,.02)}
.ctbl th{color:var(--m);font-weight:600;padding:8px 8px 7px;font-size:10px;
         text-transform:uppercase;cursor:pointer;white-space:nowrap;
         text-align:right;user-select:none;border-bottom:2px solid var(--b)}
.ctbl th:nth-child(1),.ctbl th:nth-child(2){text-align:left}
.ctbl th.sort-asc::after{content:' \25b2';font-size:8px}
.ctbl th.sort-desc::after{content:' \25bc';font-size:8px}
.ctbl td{padding:6px 8px;border-bottom:1px solid var(--b);text-align:right;
         overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ctbl td:nth-child(1){text-align:left;color:var(--m);font-variant-numeric:tabular-nums}
.ctbl td:nth-child(2){text-align:left}
.ctbl tr:last-child td{border:none}
.ctbl tbody tr:hover{background:rgba(255,255,255,.025)}
.dim{color:var(--m)!important;font-weight:normal!important}
.bold{font-weight:700}
.empty-cell{text-align:left!important;color:var(--m);font-style:italic;padding:16px 18px!important}
.clip-link{color:var(--m);text-decoration:none;font-size:13px}
.clip-link:hover{color:var(--p)}
.loading{color:var(--m);font-size:13px;font-style:italic;padding:40px;text-align:center}
</style>
</head>
<body>

<header>
  <a href="/" class="back">&#8592; Live</a>
  <span class="htitle">&#128202; Historique des sessions</span>
  <span class="hsub" id="hsub"></span>
</header>

<main id="main">
  <div class="loading">Chargement&#8230;</div>
</main>

<script>
const allSessions=[];
const sortStates={};

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function fmtDate(iso){
  if(!iso)return'—';
  return new Date(iso).toLocaleString('fr-FR',{
    day:'2-digit',month:'2-digit',year:'numeric',
    hour:'2-digit',minute:'2-digit',timeZone:'Europe/Paris'
  });
}

function fmtTime(iso){
  if(!iso)return'—';
  return new Date(iso).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit',timeZone:'Europe/Paris'});
}

function fmtDur(start,end){
  if(!end)return'en cours';
  const mins=Math.round((new Date(end)-new Date(start))/60000);
  if(mins<60)return mins+'min';
  return Math.floor(mins/60)+'h'+String(mins%60).padStart(2,'0');
}

function sigCell(val,color){
  if(!val||+val<=0)return`<td class="dim">—</td>`;
  return`<td style="color:${color};font-weight:600">${Math.round(+val)}</td>`;
}

function renderTbody(sid){
  const s=allSessions.find(x=>x.id===sid);
  if(!s||!s.clips||!s.clips.length)return'<tr><td colspan="10" class="empty-cell">Aucun clip pour cette session.</td></tr>';
  const st=sortStates[sid];
  const sorted=[...s.clips].sort((a,b)=>st.dir*((+a[st.col]||0)-(+b[st.col]||0)));
  return sorted.map(c=>`<tr>
    <td>${fmtTime(c.created_at)}</td>
    <td title="${esc(c.title)}">${esc(c.title)}</td>
    <td class="bold">${Math.round(+c.composite_score||0)}</td>
    ${sigCell(c.v_score,'#9147ff')}
    ${sigCell(c.e_score,'#f0a500')}
    ${sigCell(c.u_score,'#00c2c2')}
    ${sigCell(c.c_score,'#ff6b6b')}
    ${sigCell(c.r_score,'#a8ff78')}
    <td class="dim">${(+c.duration||0).toFixed(0)}s</td>
    <td><a href="${esc(c.url)}" target="_blank" class="clip-link">↗</a></td>
  </tr>`).join('');
}

function renderHeaders(sid){
  const st=sortStates[sid];
  const cols=[
    {key:'created_at',label:'Heure',color:''},
    {key:'title',label:'Titre',color:''},
    {key:'composite_score',label:'Score',color:''},
    {key:'v_score',label:'V',color:'#9147ff'},
    {key:'e_score',label:'E',color:'#f0a500'},
    {key:'u_score',label:'U',color:'#00c2c2'},
    {key:'c_score',label:'C',color:'#ff6b6b'},
    {key:'r_score',label:'R',color:'#a8ff78'},
    {key:'duration',label:'Dur',color:''},
  ];
  return cols.map(c=>{
    const sc=c.key===st.col?(st.dir>0?' sort-asc':' sort-desc'):'';
    const numCls=c.key!=='created_at'&&c.key!=='title'?' num':'';
    const cs=c.color?` style="color:${c.color}"`:''
    return`<th data-sid="${sid}" data-col="${c.key}" class="${numCls}${sc}"${cs}>${c.label}</th>`;
  }).join('')+'<th></th>';
}

function renderSession(s,idx){
  sortStates[s.id]={col:'composite_score',dir:-1};
  const dur=fmtDur(s.started_at,s.ended_at);
  const top=(+s.top_score||0).toFixed(0);
  const div=document.createElement('div');
  div.className='scard'+(idx===0?' open':'');
  div.innerHTML=`
    <div class="scard-head" onclick="this.closest('.scard').classList.toggle('open')">
      <span class="schan">${esc(s.streamer)}</span>
      <span class="smeta">${fmtDate(s.started_at)}</span>
      <span class="sdur">${dur}</span>
      <span class="scount">${s.clip_count} clip${s.clip_count!==1?'s':''}</span>
      <span class="stop">&#11088; ${top}</span>
      <span class="stoggle">&#9660;</span>
    </div>
    <div class="scard-body">
      <table class="ctbl" id="stbl-${s.id}">
        <colgroup>
          <col class="c-time"><col class="c-title">
          <col class="c-score"><col class="c-sig"><col class="c-sig">
          <col class="c-sig"><col class="c-sig"><col class="c-sig">
          <col class="c-dur"><col class="c-link">
        </colgroup>
        <thead><tr id="shd-${s.id}">${renderHeaders(s.id)}</tr></thead>
        <tbody id="stbd-${s.id}">${renderTbody(s.id)}</tbody>
      </table>
    </div>`;
  return div;
}

// Event delegation for sort clicks
document.getElementById('main').addEventListener('click',e=>{
  const th=e.target.closest('th[data-col][data-sid]');
  if(!th)return;
  const sid=+th.dataset.sid,col=th.dataset.col;
  const st=sortStates[sid];
  if(!st)return;
  st.col===col?st.dir*=-1:(st.col=col,st.dir=-1);
  document.getElementById('stbd-'+sid).innerHTML=renderTbody(sid);
  document.querySelectorAll(`#shd-${sid} th[data-col]`).forEach(t=>{
    t.classList.remove('sort-asc','sort-desc');
    if(t.dataset.col===st.col)t.classList.add(st.dir>0?'sort-asc':'sort-desc');
  });
});

async function load(){
  try{
    const r=await fetch('/api/replay');
    const sessions=await r.json();
    const main=document.getElementById('main');
    main.innerHTML='';
    if(!sessions.length){
      main.innerHTML='<div class="loading">Aucune session en base.</div>';return;
    }
    document.getElementById('hsub').textContent=
      sessions.length+' session'+(sessions.length>1?'s':'')+
      ' · '+sessions.reduce((a,s)=>a+(+s.clip_count||0),0)+' clips au total';
    sessions.forEach((s,i)=>{allSessions.push(s);main.appendChild(renderSession(s,i))});
  }catch(e){
    document.getElementById('main').innerHTML='<div class="loading">Erreur de chargement.</div>';
  }
}
load();
</script>
</body>
</html>"""
