import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

function setTheme(theme){ document.documentElement.setAttribute("data-theme", theme || "dark"); }

async function toggleTheme(){
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = (cur === "dark") ? "light" : "dark";
  setTheme(next);
}

$("#loginThemeBtn").addEventListener("click", toggleTheme);

(async ()=>{
  const {r, data} = await api("/api/bootstrap");
  if (!r.ok || !data?.ok) return;
  setTheme(data.theme || "dark");
  $("#dockerPill").textContent = data.dockerpresent ? "OK" : "OFF";
  $("#dockerPill").className = `pill ${data.dockerpresent ? "ok":"warn"}`;
  if (data.first_run) location.href = "/setup";
  if (data.authed) location.href = "/home";
})();

$("#loginForm").addEventListener("submit", async (e)=>{
  e.preventDefault();
  $("#loginError").style.display = "none";

  const fd = new FormData($("#loginForm"));
  const login = String(fd.get("login") || "").trim();
  const password = String(fd.get("password") || "");

  const {r, data} = await api("/api/login", {method:"POST", json:{login, password}});
  if (!r.ok || !data?.ok){
    $("#loginErrorText").textContent = "Неверный логин или пароль";
    $("#loginError").style.display = "flex";
    return;
  }
  location.href = "/home";
});
