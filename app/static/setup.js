import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

function setTheme(theme){ document.documentElement.setAttribute("data-theme", theme || "dark"); }

async function toggleTheme(){
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = (cur === "dark") ? "light" : "dark";
  setTheme(next);
}

$("#setupThemeBtn").addEventListener("click", toggleTheme);

(async ()=>{
  const {r, data} = await api("/api/bootstrap");
  if (!r.ok || !data?.ok) return;
  setTheme(data.theme || "dark");
  $("#dockerPill").textContent = data.dockerpresent ? "OK" : "OFF";
  $("#dockerPill").className = `pill ${data.dockerpresent ? "ok":"warn"}`;
  if (!data.first_run) location.href = data.authed ? "/home" : "/login";
})();

$("#setupForm").addEventListener("submit", async (e)=>{
  e.preventDefault();
  $("#setupError").style.display = "none";

  const fd = new FormData($("#setupForm"));
  const login = String(fd.get("login") || "").trim();
  const password = String(fd.get("password") || "");

  const {r, data} = await api("/api/setup", {method:"POST", json:{login, password}});
  if (!r.ok || !data?.ok){
    $("#setupErrorText").textContent = "Не удалось создать администратора (логин>=3, пароль>=6)";
    $("#setupError").style.display = "flex";
    return;
  }
  location.href = "/home";
});
