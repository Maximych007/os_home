import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));

const state = { appid: null, logsFollow: true };

function setTab(tab){
  $$(".tab", $("#appTabs")).forEach(t=>t.classList.toggle("active", t.dataset.tab===tab));
  ["overview","settings","containers","logs"].forEach(x=>{
    $("#appTab-"+x).classList.toggle("hide", x!==tab);
  });
}

async function refresh(){
  const {r, data} = await api(`/api/apps/${encodeURIComponent(state.appid)}`);
  if (!r.ok || !data?.ok) return;

  const app = data.app;
  $("#appDetailTitle").textContent = app.title || app.id;
  $("#appDetailSub").textContent = app.id;

  $("#appOpenBtn").disabled = !app.url;
  $("#appOpenBtn").onclick = ()=>{ if (app.url) window.open(app.url, "_blank", "noreferrer"); };

  $("#appToggleBtn").textContent = app.running ? "Остановить" : "Запустить";
  $("#appToggleBtn").onclick = async ()=>{
    await api(`/api/apps/${encodeURIComponent(state.appid)}/action`, {method:"POST", json:{action: app.running ? "stop":"start"}});
    await refresh();
  };

  $("#appRestartBtn").onclick = async ()=>{
    await api(`/api/apps/${encodeURIComponent(state.appid)}/action`, {method:"POST", json:{action:"restart"}});
  };

  $("#appRemoveBtn").onclick = async ()=>{
    await api(`/api/apps/${encodeURIComponent(state.appid)}/action`, {method:"POST", json:{action:"down"}});
    location.href = "/apps";
  };

  $("#appOverviewKv").innerHTML = `
    <div class="kvrow"><span class="kvk">Статус</span><span class="kvv">${app.running ? `<span class="pill ok">работает</span>` : `<span class="pill warn">остановлено</span>`}</span></div>
    <div class="kvrow"><span class="kvk">Web UI</span><span class="kvv mono">${app.url || "—"}</span></div>
  `;

  const env = app.env || {};
  $("#envTable").innerHTML = Object.keys(env).length
    ? Object.entries(env).map(([k,v])=>`<tr><td class="mono">${k}</td><td class="mono">${v}</td></tr>`).join("")
    : `<tr><td class="muted" colspan="2">Нет</td></tr>`;

  const ports = app.ports || [];
  $("#portsTable").innerHTML = ports.length
    ? ports.map(p=>`<tr><td class="mono">${p.container}</td><td class="mono">${p.host}</td><td class="mono">${p.proto}</td></tr>`).join("")
    : `<tr><td class="muted" colspan="3">Нет</td></tr>`;

  const vols = app.volumes || [];
  $("#volumesTable").innerHTML = vols.length
    ? vols.map(v=>`<tr><td class="mono">${v.host}</td><td class="mono">${v.container}</td><td class="mono">${v.mode}</td></tr>`).join("")
    : `<tr><td class="muted" colspan="3">Нет</td></tr>`;

  const containers = app.containers || [];
  $("#containersList").innerHTML = containers.length
    ? containers.map(c=>`<div class="kvrow"><span class="kvk mono">${c.name}</span><span class="kvv">${c.status}</span></div>`).join("")
    : `<div class="muted">Нет контейнеров</div>`;

  $("#containerSelect").innerHTML = "";
  containers.forEach(c=>{
    const opt = document.createElement("option");
    opt.value = c.name;
    opt.textContent = c.name;
    $("#containerSelect").appendChild(opt);
  });

  await refreshLogs();
}

async function refreshLogs(){
  const container = $("#containerSelect").value;
  if (!container) { $("#containerLogPre").textContent = ""; return; }

  const {r, data} = await api(`/api/apps/${encodeURIComponent(state.appid)}/logs?container=${encodeURIComponent(container)}&tail=400`);
  if (!r.ok || !data?.ok) { $("#containerLogPre").textContent = ""; return; }

  $("#containerLogPre").textContent = data.text || "";
  if (state.logsFollow){
    const box = $("#containerLogPre").parentElement;
    box.scrollTop = box.scrollHeight;
  }
}

$("#appTabs").addEventListener("click", (e)=>{
  const t = e.target.closest(".tab");
  if (!t) return;
  setTab(t.dataset.tab);
});
$("#logsRefreshBtn").addEventListener("click", refreshLogs);
$("#containerSelect").addEventListener("change", refreshLogs);
$("#logsFollowBtn").addEventListener("click", ()=>{
  state.logsFollow = !state.logsFollow;
  $("#logsFollowBtn").textContent = state.logsFollow ? "Автопрокрутка: ВКЛ" : "Автопрокрутка: ВЫКЛ";
});

state.appid = $("#appRoot").dataset.appid;
setTab("overview");
refresh();
setInterval(()=>{ if (!$("#appTab-logs").classList.contains("hide")) refreshLogs(); }, 2500);
