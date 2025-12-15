import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

const state = { appsAll: [], appsView: [] };

function appStatusPill(app){
  if (!app.installed) return `<span class="pill warn">не установлено</span>`;
  return app.running ? `<span class="pill ok">работает</span>` : `<span class="pill warn">остановлено</span>`;
}

function applyAppsFilter(){
  const q = String($("#globalSearch")?.value || "").trim().toLowerCase();
  if (!q){ state.appsView = [...state.appsAll]; return; }
  state.appsView = state.appsAll.filter(a => (`${a.title} ${a.id} ${(a.tags||[]).join(" ")}`.toLowerCase().includes(q)));
}

function renderInstalled(){
  const wrap = $("#appsInstalledCards");
  wrap.innerHTML = "";

  const installed = state.appsView.filter(a=>a.installed);
  $("#installedCountPill").textContent = String(state.appsAll.filter(a=>a.installed).length);

  if (!installed.length){
    wrap.innerHTML = `<div class="muted">Ничего не найдено.</div>`;
    return;
  }

  for (const app of installed){
    const el = document.createElement("div");
    el.className = "appcard";
    el.innerHTML = `
      <div class="appHead">
        <div class="appLeft">
          <div class="appIcon"><img class="appiconimg" src="${app.icon_url}" alt="${app.title}"></div>
          <div>
            <div class="appTitle">${app.title}</div>
            <div class="appId mono">${app.id}</div>
          </div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px; align-items:flex-end">
          ${appStatusPill(app)}
          <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end">
            <button class="btn" data-act="toggle">${app.running ? "Остановить":"Запустить"}</button>
            <a class="btn primary ${app.url ? "" : "btndisabled"}" href="${app.url || "#"}" target="_blank" rel="noreferrer">Web UI</a>
            <a class="btn" href="/apps/${encodeURIComponent(app.id)}">Детали</a>
          </div>
        </div>
      </div>
      <div class="appDesc">${app.desc || ""}</div>
      <div class="chips">${(app.tags||[]).map(t=>`<span class="pill">${t}</span>`).join("")}</div>
    `;

    el.addEventListener("click", async (e)=>{
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.act === "toggle"){
        await api(`/api/apps/${encodeURIComponent(app.id)}/action`, {method:"POST", json:{action: app.running ? "stop":"start"}});
        await refresh();
      }
    });

    wrap.appendChild(el);
  }
}

function renderCatalog(){
  const q = String($("#catalogSearch").value || "").trim().toLowerCase();
  const list = state.appsAll.filter(a=>{
    const s = `${a.title||""} ${a.id||""} ${(a.tags||[]).join(" ")}`.toLowerCase();
    return s.includes(q);
  });

  const wrap = $("#catalogGrid");
  wrap.innerHTML = "";

  for (const app of list){
    const el = document.createElement("div");
    el.className = "appcard";
    el.innerHTML = `
      <div class="appHead">
        <div class="appLeft">
          <div class="appIcon"><img class="appiconimg" src="${app.icon_url}" alt="${app.title}"></div>
          <div>
            <div class="appTitle">${app.title}</div>
            <div class="appId mono">${app.id}</div>
          </div>
        </div>
        <div style="display:flex; flex-direction:column; gap:8px; align-items:flex-end">
          ${app.installed ? `<span class="pill ok">установлено</span>` : `<span class="pill warn">не установлено</span>`}
          <button class="btn primary" ${app.installed ? "disabled":""} data-act="install">Установить</button>
        </div>
      </div>
      <div class="appDesc">${app.desc || ""}</div>
      <div class="chips">${(app.tags||[]).map(t=>`<span class="pill">${t}</span>`).join("")}</div>
    `;

    el.addEventListener("click", async (e)=>{
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.act === "install"){
        await api(`/api/apps/${encodeURIComponent(app.id)}/install`, {method:"POST"});
        await refresh();
      }
    });

    wrap.appendChild(el);
  }
}

async function refresh(){
  const {r, data} = await api("/api/apps");
  if (!r.ok || !data?.ok) return;
  state.appsAll = data.apps || [];
  applyAppsFilter();
  renderInstalled();
  renderCatalog();
}

$("#appsRefreshBtn").addEventListener("click", refresh);
$("#globalSearch").addEventListener("input", ()=>{ applyAppsFilter(); renderInstalled(); });

$("#openInstallModalBtn").addEventListener("click", ()=>$("#installModalWrap").classList.add("show"));
$("#installCloseBtn").addEventListener("click", ()=>$("#installModalWrap").classList.remove("show"));
$("#installModalWrap").addEventListener("click", (e)=>{ if (e.target === $("#installModalWrap")) $("#installModalWrap").classList.remove("show"); });
$("#catalogSearch").addEventListener("input", renderCatalog);

refresh();
