import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

function jobRow(j){
  const cls = (j.status==="success") ? "ok" : ((j.status==="running"||j.status==="queued") ? "warn" : "bad");
  const act = j.action || j.kind;
  return `
    <div class="kvrow">
      <span class="kvk mono">${j.appid}</span>
      <span class="kvv" style="display:inline-flex; gap:10px; align-items:center; flex-wrap:wrap">
        <span class="pill ${cls}">${act}</span>
        <span class="muted">${j.message || ""}</span>
      </span>
    </div>
  `;
}

async function refresh(){
  const {r, data} = await api("/api/jobs?limit=80");
  if (!r.ok || !data?.ok) return;
  $("#jobsFull").innerHTML = (data.jobs || []).map(jobRow).join("") || `<div class="muted">Нет задач.</div>`;
  if ($("#jobsCountPill")) $("#jobsCountPill").textContent = String((data.jobs || []).length);
}

refresh();
setInterval(refresh, 4000);
