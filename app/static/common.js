import { api } from "./api.js";
const $ = (s, r=document) => r.querySelector(s);

function setTheme(theme){ document.documentElement.setAttribute("data-theme", theme || "dark"); }

async function toggleTheme(){
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = (cur === "dark") ? "light" : "dark";
  setTheme(next);
  await api("/api/theme", {method:"POST", json:{theme: next}});
}

function openMobileMenu(){ $("#sidebar")?.classList.add("open"); $("#backdrop")?.classList.add("show"); }
function closeMobileMenu(){ $("#sidebar")?.classList.remove("open"); $("#backdrop")?.classList.remove("show"); }

$("#menuBtn")?.addEventListener("click", ()=>{
  const sb = $("#sidebar");
  if (!sb) return;
  sb.classList.contains("open") ? closeMobileMenu() : openMobileMenu();
});
$("#backdrop")?.addEventListener("click", closeMobileMenu);

$("#topThemeBtn")?.addEventListener("click", toggleTheme);

// общие “пилюли”
(async ()=>{
  const {r, data} = await api("/api/bootstrap");
  if (!r.ok || !data?.ok) return;

  setTheme(data.theme || "dark");

  if ($("#sessionPill")) $("#sessionPill").textContent = data.user || "—";
  const dockerOk = !!data.dockerpresent;
  const pill = $("#dockerOkPill");
  if (pill){
    pill.textContent = dockerOk ? "Docker: OK" : "Docker: OFF";
    pill.className = `pill ${dockerOk ? "ok":"warn"}`;
  }
})();
