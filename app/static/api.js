export async function api(url, {method="GET", json=null} = {}) {
  const init = { method, headers: {"Accept":"application/json"} };
  if (json !== null) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  }
  const r = await fetch(url, init);
  let data = null;
  try { data = await r.json(); } catch {}
  return { r, data };
}
