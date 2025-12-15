import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));

const GRIDCOLS = 4;
const MAXH = 2;

const WIDGETCATALOG = [
  {id:"cpu", title:"CPU", desc:"Нагрузка процессора", defaultW:2, defaultH:1},
  {id:"ram", title:"RAM", desc:"Оперативная память", defaultW:2, defaultH:1},
  {id:"disk", title:"Диск", desc:"Хранилище", defaultW:4, defaultH:1},
  {id:"temp", title:"Температура", desc:"Температура CPU", defaultW:2, defaultH:1},
  {id:"uptime", title:"Аптайм", desc:"Время работы", defaultW:2, defaultH:1},
  {id:"net", title:"Сеть", desc:"Трафик", defaultW:4, defaultH:1},
];

const state = {
  tilesMap: {},
  appsAll: [],
  jobs: [],
  widgets: [],
  layout: [],
  saveTimer: null,
  logsFollow: true,
};

// ---------- layout helpers ----------
function clamp(min, max, v){ return Math.max(min, Math.min(max, v)); }
function clampW(w){ return clamp(1, GRIDCOLS, Math.round(w)); }
function clampH(h){ return clamp(1, MAXH, Math.round(h)); }
function rectsOverlap(a,b){ return !(a.x+a.w<=b.x || b.x+b.w<=a.x || a.y+a.h<=b.y || b.y+b.h<=a.y); }
function within(it){ return it.x>=0 && it.y>=0 && it.w>=1 && it.h>=1 && (it.x+it.w)<=GRIDCOLS; }

function packLayout(items, preferredKey=null){
  const out = [];
  const input = items.map(x=>({
    key:x.key, x:Math.max(0,x.x|0), y:Math.max(0,x.y|0),
    w:clampW(x.w ?? 2), h:clampH(x.h ?? 1),
  }));

  input.sort((a,b)=>{
    if (preferredKey){
      if (a.key===preferredKey && b.key!==preferredKey) return -1;
      if (b.key===preferredKey && a.key!==preferredKey) return 1;
    }
    return (a.y-b.y) || (a.x-b.x);
  });

  for (const it0 of input){
    const it = {...it0};
    it.w = clampW(it.w); it.h = clampH(it.h);
    it.x = clamp(0, GRIDCOLS-it.w, it.x);

    while (true){
      if (!within(it)) it.x = clamp(0, GRIDCOLS-it.w, it.x);
      let collide = false;
      for (const placed of out){
        if (rectsOverlap(it, placed)) { collide=true; break; }
      }
      if (!collide) break;
      it.y += 1;
    }
    out.push(it);
  }

  out.sort((a,b)=>(a.y-b.y)||(a.x-b.x));
  return out;
}

function computeOrderFromLayout(){
  return [...state.layout]
    .sort((a,b)=>(a.y-b.y)||(a.x-b.x))
    .map(x=>x.key);
}

function scheduleWidgetsSave(){
  if (state.saveTimer) clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(async ()=>{
    state.widgets = computeOrderFromLayout();
    await api("/api/widgets/config", {method:"POST", json:{widgets: state.widgets, layout: state.layout}});
    state.saveTimer = null;
  }, 500);
}

// ---------- widgets modal ----------
function renderPalette(){
  $("#paletteGrid").innerHTML = "";
  for (const w of WIDGETCATALOG){
    const has = state.widgets.includes(w.id);
    const el = document.createElement("div");
    el.className = "pItem";
    el.draggable = true;
    el.dataset.wid = w.id;
    el.innerHTML = `
      <div style="min-width:0">
        <div class="pTitle">${w.title}</div>
        <div class="pDesc">${w.desc}</div>
      </div>
      <span class="pill ${has ? "ok":"warn"} pBadge">${has ? "добавлен":"добавить"}</span>
    `;
    el.addEventListener("dragstart", (e)=>{
      e.dataTransfer.setData("text/plain", JSON.stringify({type:"palette", key:w.id}));
      e.dataTransfer.effectAllowed = "copy";
    });
    $("#paletteGrid").appendChild(el);
  }
}

function renderCanvas(){
  const canvas = $("#widgetCanvas");
  canvas.innerHTML = "";
  if (!state.layout.length){
    canvas.innerHTML = `<div class="muted" style="grid-column:1/-1; padding:10px">Пусто. Перетащи виджет из палитры.</div>`;
    return;
  }

  for (const item of state.layout){
    const meta = WIDGETCATALOG.find(w=>w.id===item.key);
    const card = document.createElement("div");
    card.className = "wCard";
    card.dataset.key = item.key;
    card.style.gridColumn = `${item.x+1} / span ${item.w}`;
    card.style.gridRow = `${item.y+1} / span ${item.h}`;
    card.innerHTML = `
      <div class="wTop">
        <div style="min-width:0">
          <div class="wName">${meta ? meta.title : item.key}</div>
          <div class="wMini">${meta ? meta.desc : ""}</div>
        </div>
        <div class="wBtns">
          <button class="wX" title="Удалить" type="button">×</button>
        </div>
      </div>
      <div class="wResize" title="Resize"></div>
    `;

    card.querySelector(".wX").addEventListener("click", (e)=>{
      e.stopPropagation();
      state.widgets = state.widgets.filter(x=>x!==item.key);
      state.layout = state.layout.filter(x=>x.key!==item.key);
      state.layout = packLayout(state.layout);
      renderPalette(); renderCanvas(); renderHomeTiles();
      scheduleWidgetsSave();
    });

    card.addEventListener("pointerdown", (e)=>{
      if (e.target.closest(".wX")) return;
      if (e.target.closest(".wResize")) return;
      startDragMove(e, item.key);
    });

    card.querySelector(".wResize").addEventListener("pointerdown", (e)=>{
      e.stopPropagation();
      startDragResize(e, item.key);
    });

    canvas.appendChild(card);
  }
}

function bindCanvasDrop(){
  const canvas = $("#widgetCanvas");
  canvas.addEventListener("dragover", (e)=>{
    e.preventDefault();
    canvas.classList.add("dropGlow");
  });
  canvas.addEventListener("dragleave", ()=> canvas.classList.remove("dropGlow"));
  canvas.addEventListener("drop", (e)=>{
    e.preventDefault();
    canvas.classList.remove("dropGlow");

    let payload=null;
    try { payload = JSON.parse(e.dataTransfer.getData("text/plain")||"null"); } catch {}
    if (!payload || payload.type !== "palette") return;

    const key = payload.key;
    if (state.widgets.includes(key)) return;

    const meta = WIDGETCATALOG.find(w=>w.id===key);
    const w = meta?.defaultW ?? 2;
    const h = meta?.defaultH ?? 1;

    state.widgets.push(key);
    state.layout.push({key, x:0, y:0, w, h});
    state.layout = packLayout(state.layout, key);

    renderPalette(); renderCanvas(); renderHomeTiles();
    scheduleWidgetsSave();
  });
}

function getGridMetrics(canvasEl){
  const r = canvasEl.getBoundingClientRect();
  const cs = getComputedStyle(canvasEl);
  const gap = parseFloat(cs.gap || "14");
  const padL = parseFloat(cs.paddingLeft || "0");
  const padT = parseFloat(cs.paddingTop || "0");
  const padR = parseFloat(cs.paddingRight || "0");
  const innerW = r.width - padL - padR;
  const colW = (innerW - gap*(GRIDCOLS-1)) / GRIDCOLS;
  const rowH = parseFloat(cs.gridAutoRows || "92");
  return {r, gap, padL, padT, colW, rowH};
}
function pointToCell(canvasEl, clientX, clientY){
  const m = getGridMetrics(canvasEl);
  const x0 = clientX - m.r.left - m.padL;
  const y0 = clientY - m.r.top - m.padT;
  const stepX = m.colW + m.gap;
  const stepY = m.rowH + m.gap;
  const gx = Math.floor((x0 + m.colW*0.5) / stepX);
  const gy = Math.floor((y0 + m.rowH*0.5) / stepY);
  return {x: clamp(0, GRIDCOLS-1, gx), y: Math.max(0, gy)};
}
function makeGhost(canvasEl, item){
  const ghost = document.createElement("div");
  ghost.className = "wGhost";
  ghost.style.gridColumn = `${item.x+1} / span ${item.w}`;
  ghost.style.gridRow = `${item.y+1} / span ${item.h}`;
  canvasEl.appendChild(ghost);
  return ghost;
}

function startDragMove(e, key){
  const canvas = $("#widgetCanvas");
  const card = $(`.wCard[data-key="${CSS.escape(key)}"]`, canvas);
  if (!card) return;

  const item = state.layout.find(x=>x.key===key);
  const drag = {active:true, pointerId:e.pointerId, startX:e.clientX, startY:e.clientY, ghostEl:makeGhost(canvas, item)};

  card.setPointerCapture(e.pointerId);
  card.classList.add("dragging");
  card.style.zIndex = 30;

  const onMove = (ev)=>{
    if (!drag.active) return;
    const dx = ev.clientX - drag.startX;
    const dy = ev.clientY - drag.startY;
    card.style.transform = `translate(${dx}px, ${dy}px)`;

    const cell = pointToCell(canvas, ev.clientX, ev.clientY);
    const cur = state.layout.find(x=>x.key===key);
    const target = {...cur, x: clamp(0, GRIDCOLS-cur.w, cell.x), y: cell.y};
    if (cur.x===target.x && cur.y===target.y) return;

    const candidate = state.layout.map(x=>x.key===key ? target : {...x});
    state.layout = packLayout(candidate, key);

    const pk = state.layout.find(x=>x.key===key);
    drag.ghostEl.style.gridColumn = `${pk.x+1} / span ${pk.w}`;
    drag.ghostEl.style.gridRow = `${pk.y+1} / span ${pk.h}`;

    for (const it of state.layout){
      const c = $(`.wCard[data-key="${CSS.escape(it.key)}"]`, canvas);
      if (!c || it.key===key) continue;
      c.style.gridColumn = `${it.x+1} / span ${it.w}`;
      c.style.gridRow = `${it.y+1} / span ${it.h}`;
    }
  };

  const onUp = ()=>{
    if (!drag.active) return;
    drag.active = false;
    card.classList.remove("dragging");
    card.style.transform = "";
    card.style.zIndex = "";
    try { card.releasePointerCapture(drag.pointerId); } catch {}
    drag.ghostEl.remove();
    renderPalette(); renderCanvas(); renderHomeTiles();
    scheduleWidgetsSave();
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

function startDragResize(e, key){
  const canvas = $("#widgetCanvas");
  const card = $(`.wCard[data-key="${CSS.escape(key)}"]`, canvas);
  if (!card) return;

  const base = state.layout.find(x=>x.key===key);
  if (!base) return;

  const drag = {active:true, pointerId:e.pointerId, startX:e.clientX, startY:e.clientY, ghostEl:makeGhost(canvas, base)};
  card.setPointerCapture(e.pointerId);
  card.classList.add("dragging");
  card.style.zIndex = 30;

  const m = getGridMetrics(canvas);
  const stepX = m.colW + m.gap;
  const stepY = m.rowH + m.gap;

  const onMove = (ev)=>{
    if (!drag.active) return;
    const dx = ev.clientX - drag.startX;
    const dy = ev.clientY - drag.startY;
    const wStep = Math.round(dx / stepX);
    const hStep = Math.round(dy / stepY);

    const cur = state.layout.find(x=>x.key===key);
    let newW = clampW(cur.w + wStep);
    let newH = clampH(cur.h + hStep);
    const newX = clamp(0, GRIDCOLS - newW, cur.x);

    const target = {...cur, x:newX, w:newW, h:newH};
    const candidate = state.layout.map(x=>x.key===key ? target : {...x});
    state.layout = packLayout(candidate, key);

    const pk = state.layout.find(x=>x.key===key);
    drag.ghostEl.style.gridColumn = `${pk.x+1} / span ${pk.w}`;
    drag.ghostEl.style.gridRow = `${pk.y+1} / span ${pk.h}`;

    for (const it of state.layout){
      const c = $(`.wCard[data-key="${CSS.escape(it.key)}"]`, canvas);
      if (!c || it.key===key) continue;
      c.style.gridColumn = `${it.x+1} / span ${it.w}`;
      c.style.gridRow = `${it.y+1} / span ${it.h}`;
    }
  };

  const onUp = ()=>{
    if (!drag.active) return;
    drag.active = false;
    card.classList.remove("dragging");
    card.style.zIndex = "";
    try { card.releasePointerCapture(drag.pointerId); } catch {}
    drag.ghostEl.remove();
    renderPalette(); renderCanvas(); renderHomeTiles();
    scheduleWidgetsSave();
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };

  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

// ---------- home tiles ----------
function tileSpanClass(w){ return w===1?"w1":w===2?"w2":w===3?"w3":"w4"; }
function tileHeightClass(h){ return h===2?"h2":"h1"; }

function renderHomeTiles(){
  const wrap = $("#tiles");
  wrap.innerHTML = "";

  const sorted = [...state.layout].sort((a,b)=>(a.y-b.y)||(a.x-b.x));
  if (!sorted.length){
    wrap.innerHTML = `<div class="muted">Виджеты не настроены.</div>`;
    return;
  }

  for (const item of sorted){
    const meta = WIDGETCATALOG.find(w=>w.id===item.key);
    const d = state.tilesMap[item.key] || {value:"—", unit:"", sub:"", pct:null};

    const el = document.createElement("div");
    el.className = `tile ${tileSpanClass(item.w)} ${tileHeightClass(item.h)}`;
    el.innerHTML = `
      <div class="tileHead"><div class="tileTitle"></div><div></div></div>
      <div class="tileValue"><div class="tileNumber"></div><div class="tileUnit"></div></div>
      <div class="tileSub"></div>
      <div class="bar" style="display:none"><div class="barFill"></div></div>
    `;
    el.querySelector(".tileTitle").textContent = meta ? meta.title : item.key;
    el.querySelector(".tileNumber").textContent = d.value ?? "—";
    el.querySelector(".tileUnit").textContent = d.unit ?? "";
    el.querySelector(".tileSub").textContent = d.sub ?? "";

    const pct = (typeof d.pct === "number") ? clamp(0,100,d.pct) : null;
    if (pct !== null){
      el.querySelector(".bar").style.display = "";
      el.querySelector(".barFill").style.width = pct + "%";
    }
    wrap.appendChild(el);
  }
}

// ---------- launcher + jobs preview ----------
function renderHomeLauncher(){
  const wrap = $("#homeLauncher");
  wrap.innerHTML = "";
  const installed = state.appsAll.filter(a=>a.installed);

  if (!installed.length){
    wrap.innerHTML = `<div class="muted">Нет установленных приложений.</div>`;
    return;
  }

  for (const app of installed){
    const el = document.createElement("div");
    el.className = "appshot";
    el.innerHTML = `<div class="appshoticon"></div><div class="appshotlabel"></div>`;
    const icon = el.querySelector(".appshoticon");
    const img = document.createElement("img");
    img.className = "appshotimg";
    img.src = app.icon_url;
    img.alt = app.title;
    icon.appendChild(img);
    el.querySelector(".appshotlabel").textContent = app.title;

    el.addEventListener("click", ()=>{
      if (app.url) window.open(app.url, "_blank", "noreferrer");
      else location.href = `/apps/${encodeURIComponent(app.id)}`;
    });

    wrap.appendChild(el);
  }
}

function renderJobsPreview(){
  const wrap = $("#jobsPreview");
  const row = (j)=>{
    const cls = (j.status==="success") ? "ok" : ((j.status==="running"||j.status==="queued") ? "warn" : "bad");
    return `
      <div class="kvrow">
        <span class="kvk mono">${j.appid}</span>
        <span class="kvv" style="display:inline-flex; gap:10px; align-items:center; flex-wrap:wrap">
          <span class="pill ${cls}">${j.action || j.kind}</span>
          <span class="muted">${j.message || ""}</span>
        </span>
      </div>
    `;
  };
  wrap.innerHTML = state.jobs.slice(0,5).map(row).join("") || `<div class="muted">Нет задач.</div>`;
  if ($("#jobsCountPill")) $("#jobsCountPill").textContent = String(state.jobs.length);
}

// ---------- refresh ----------
async function refreshWidgetsConfig(){
  const {r, data} = await api("/api/widgets/config");
  if (!r.ok || !data?.ok) return;

  state.widgets = data.config.widgets || [];
  state.layout = packLayout(data.config.layout || []);
}

async function refreshTiles(){
  const {r, data} = await api("/api/tiles");
  if (!r.ok || !data?.ok) return;
  const map = {};
  (data.tiles || []).forEach(t => { map[t.id] = t; });
  state.tilesMap = map;
  renderHomeTiles();
}

async function refreshApps(){
  const {r, data} = await api("/api/apps");
  if (!r.ok || !data?.ok) return;
  state.appsAll = data.apps || [];
  renderHomeLauncher();
  $("#installedCountPill").textContent = String(state.appsAll.filter(a=>a.installed).length);
}

async function refreshJobs(){
  const {r, data} = await api("/api/jobs?limit=50");
  if (!r.ok || !data?.ok) return;
  state.jobs = data.jobs || [];
  renderJobsPreview();
}

// ---------- modal open/close ----------
$("#openWidgetsBtnHome").addEventListener("click", ()=>{
  $("#widgetsModalWrap").classList.add("show");
  renderPalette(); renderCanvas();
});
$("#widgetsCloseBtn").addEventListener("click", ()=>$("#widgetsModalWrap").classList.remove("show"));
$("#widgetsModalWrap").addEventListener("click", (e)=>{ if (e.target===$("#widgetsModalWrap")) $("#widgetsModalWrap").classList.remove("show"); });

$("#widgetsResetBtn").addEventListener("click", ()=>{
  state.widgets = ["cpu","ram","disk","temp","uptime","net"];
  state.layout = packLayout(state.widgets.map((k,i)=>({key:k,x:0,y:i,w:(k==="disk"||k==="net")?4:2,h:1})));
  renderPalette(); renderCanvas(); renderHomeTiles();
  scheduleWidgetsSave();
});

bindCanvasDrop();

// init
(async function init(){
  await refreshWidgetsConfig();
  renderPalette(); renderCanvas(); renderHomeTiles();

  await refreshTiles();
  await refreshApps();
  await refreshJobs();

  setInterval(refreshTiles, 3000);
  setInterval(refreshJobs, 4000);
})();
