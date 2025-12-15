import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

function setUpdStatus(html){ $("#updStatus").innerHTML = html; }
function logUpd(line){
  const pre = $("#updLog");
  const t = new Date().toLocaleTimeString("ru-RU");
  pre.textContent = (pre.textContent ? pre.textContent + "\n" : "") + `[${t}] ${line}`;
  pre.parentElement.scrollTop = pre.parentElement.scrollHeight;
}

async function refreshSystemInfo(){
  const {r, data} = await api("/api/system/info");
  if (!r.ok || !data?.ok) return;

  const info = data.info || {};
  const net = data.net || {};
  const ips = (net.ips || []).map(x=>`${x.iface}: ${x.ip}`).join("<br>") || "—";

  $("#sysInfoKv").innerHTML = `
    <div class="kvrow"><span class="kvk">Версия</span><span class="kvv mono">${info.version || "—"}</span></div>
    <div class="kvrow"><span class="kvk">Python</span><span class="kvv mono">${info.python || "—"}</span></div>
    <div class="kvrow"><span class="kvk">OS</span><span class="kvv mono">${info.os || "—"}</span></div>
    <div class="kvrow"><span class="kvk">Архитектура</span><span class="kvv mono">${info.arch || "—"}</span></div>
    <div class="kvrow"><span class="kvk">Hostname</span><span class="kvv mono">${net.hostname || "—"}</span></div>
    <div class="kvrow"><span class="kvk">IP</span><span class="kvv mono">${ips}</span></div>
  `;
}

$("#backupBtn").addEventListener("click", ()=>{ location.href="/api/system/backup"; });

$("#savePassBtn").addEventListener("click", async ()=>{
  const current_password = String($("#curPass").value || "");
  const new_password = String($("#newPass").value || "");
  const {r, data} = await api("/api/system/password", {method:"POST", json:{current_password, new_password}});
  if (!r.ok || !data?.ok) return alert("Ошибка смены пароля");
  $("#curPass").value = ""; $("#newPass").value = "";
  alert("Пароль обновлён");
});

$("#checkUpdBtn").addEventListener("click", async ()=>{
  setUpdStatus(`<span class="pill warn">проверка...</span>`);
  $("#doUpdBtn").disabled = true;
  $("#updLog").textContent = "";
  logUpd("Проверка обновлений...");

  const {r, data} = await api("/api/system/update/check");
  if (!r.ok || !data?.ok){
    setUpdStatus(`<span class="pill bad">ошибка</span>`);
    return;
  }

  $("#updSupported").textContent = data.supported ? "да" : "нет";
  $("#updBehind").textContent = (typeof data.behind === "number") ? String(data.behind) : "—";

  if (!data.supported){
    setUpdStatus(`<span class="pill warn">не поддерживается</span>`);
    return;
  }
  if (data.status !== "ok"){
    setUpdStatus(`<span class="pill warn">${data.status}</span>`);
    if (data.log) logUpd(data.log);
    return;
  }
  if (data.has_update){
    setUpdStatus(`<span class="pill ok">доступно обновление</span>`);
    $("#doUpdBtn").disabled = false;
  } else {
    setUpdStatus(`<span class="pill ok">актуально</span>`);
  }
});

$("#doUpdBtn").addEventListener("click", async ()=>{
  setUpdStatus(`<span class="pill warn">обновление...</span>`);
  $("#doUpdBtn").disabled = true;
  logUpd("Применение обновления...");

  const {r, data} = await api("/api/system/update/apply", {method:"POST"});
  if (!r.ok || !data?.ok){
    setUpdStatus(`<span class="pill bad">ошибка</span>`);
    return;
  }

  if (data.status === "updated"){
    setUpdStatus(`<span class="pill ok">обновлено</span>`);
    if (data.log) logUpd(data.log);
  } else {
    setUpdStatus(`<span class="pill warn">${data.status}</span>`);
    if (data.log) logUpd(data.log);
  }
});

refreshSystemInfo();
