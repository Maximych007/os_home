import os
import asyncio
import sqlite3
import time
import json
import socket
import platform
import tempfile
import zipfile
import uuid
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import bcrypt
import psutil
import docker
from docker.errors import DockerException, NotFound

from fastapi import FastAPI, Request, UploadFile, File, Form, Body, Query
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    FileResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


VERSION = "0.6.0"

APPDIR = Path(__file__).resolve().parent
DATADIR = APPDIR / "data"
DBPATH = DATADIR / "app.db"
APPSDIR = DATADIR / "apps"
ICONSDIR = DATADIR / "icons"

SESSIONSECRET = os.environ.get("SERVER_UI_SECRET", "dev-secret-change-me")

AVAILABLETILES = {
    "cpu": "CPU",
    "ram": "RAM",
    "disk": "Диск",
    "temp": "Температура",
    "uptime": "Аптайм",
    "net": "Сеть",
}

DEFAULT_WIDGETS = ["cpu", "ram", "disk", "temp", "uptime", "net"]
DEFAULT_LAYOUT = [
    {"key": "cpu", "x": 0, "y": 0, "w": 2, "h": 1},
    {"key": "ram", "x": 2, "y": 0, "w": 2, "h": 1},
    {"key": "disk", "x": 0, "y": 1, "w": 4, "h": 1},
    {"key": "temp", "x": 0, "y": 2, "w": 2, "h": 1},
    {"key": "uptime", "x": 2, "y": 2, "w": 2, "h": 1},
    {"key": "net", "x": 0, "y": 3, "w": 4, "h": 1},
]

APPCATALOG: dict[str, Any] = {
    "qbittorrent": {
        "title": "qBittorrent",
        "description": "Торрент-клиент с веб-интерфейсом",
        "default_url": "http://localhost:8080",
        "tags": ["P2P"],
        "services": [
            {
                "name": "qbittorrent",
                "image": "linuxserver/qbittorrent:latest",
                "env": {
                    "PUID": "1000",
                    "PGID": "1000",
                    "TZ": "Asia/Yekaterinburg",
                    "WEBUI_PORT": "8080",
                },
                "ports": {"8080/tcp": 8080, "6881/tcp": 6881, "6881/udp": 6881},
                "volumes": {"config": "/config", "downloads": "/downloads"},
            }
        ],
    },
    "adguardhome": {
        "title": "AdGuard Home",
        "description": "DNS-сервер с блокировкой рекламы/трекеров",
        "default_url": "http://localhost:3000",
        "tags": ["DNS"],
        "services": [
            {
                "name": "adguardhome",
                "image": "adguard/adguardhome:latest",
                "env": {"TZ": "Asia/Yekaterinburg"},
                "ports": {"53/tcp": 53, "53/udp": 53, "3000/tcp": 3000},
                "volumes": {"work": "/opt/adguardhome/work", "conf": "/opt/adguardhome/conf"},
            }
        ],
    },
    "wg-easy": {
        "title": "WireGuard Easy",
        "description": "WireGuard VPN + Web UI",
        "default_url": "http://localhost:51821",
        "tags": ["VPN"],
        "services": [
            {
                "name": "wg-easy",
                "image": "ghcr.io/wg-easy/wg-easy:latest",
                "env": {
                    "WG_HOST": "YOUR_SERVER_IP_OR_DDNS",
                    "PASSWORD": "change-me",
                    "WG_PORT": "51820",
                },
                "ports": {"51820/udp": 51820, "51821/tcp": 51821},
                "volumes": {"config": "/etc/wireguard"},
                "cap_add": ["NET_ADMIN", "SYS_MODULE"],
                "sysctls": {"net.ipv4.ip_forward": "1", "net.ipv4.conf.all.src_valid_mark": "1"},
            }
        ],
    },
}


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSIONSECRET)
app.mount("/static", StaticFiles(directory=str(APPDIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APPDIR / "templates"))


# ---------------- DB ----------------
def db() -> sqlite3.Connection:
    DATADIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DBPATH)
    conn.row_factory = sqlite3.Row
    return conn


def initdb() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              username TEXT NOT NULL UNIQUE,
              passwordhash TEXT NOT NULL,
              createdat TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              theme TEXT NOT NULL,
              updatedat TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS widgetsconfig (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              widgets TEXT NOT NULL,
              layout TEXT NOT NULL,
              updatedat TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appicons (
              appid TEXT PRIMARY KEY,
              filename TEXT NOT NULL,
              mimetype TEXT NOT NULL,
              updatedat TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              appid TEXT NOT NULL,
              action TEXT,
              status TEXT NOT NULL,
              createdat TEXT NOT NULL,
              startedat TEXT,
              finishedat TEXT,
              message TEXT
            )
            """
        )

        cur = conn.execute("SELECT id FROM settings WHERE id=1")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO settings(id, theme, updatedat) VALUES(1, ?, ?)",
                ("dark", datetime.utcnow().isoformat()),
            )

        cur = conn.execute("SELECT id FROM widgetsconfig WHERE id=1")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO widgetsconfig(id, widgets, layout, updatedat) VALUES(1, ?, ?, ?)",
                (json.dumps(DEFAULT_WIDGETS), json.dumps(DEFAULT_LAYOUT), datetime.utcnow().isoformat()),
            )


@app.on_event("startup")
def _startup():
    initdb()
    APPSDIR.mkdir(parents=True, exist_ok=True)
    ICONSDIR.mkdir(parents=True, exist_ok=True)


# ---------------- Auth helpers ----------------
def getsingleuser():
    with db() as conn:
        return conn.execute("SELECT id, username, passwordhash, createdat FROM users WHERE id=1").fetchone()


def firstrun() -> bool:
    return getsingleuser() is None


def createsingleuser(username: str, password: str) -> None:
    pwhash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with db() as conn:
        conn.execute(
            "INSERT INTO users(id, username, passwordhash, createdat) VALUES(1, ?, ?, ?)",
            (username, pwhash, datetime.utcnow().isoformat()),
        )


def verifylogin(username: str, password: str) -> bool:
    u = getsingleuser()
    if not u:
        return False
    if u["username"] != username:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), u["passwordhash"].encode("utf-8"))


def setpassword(newpassword: str) -> None:
    pwhash = bcrypt.hashpw(newpassword.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with db() as conn:
        conn.execute("UPDATE users SET passwordhash=? WHERE id=1", (pwhash,))


def require_auth_api(request: Request) -> JSONResponse | None:
    if not request.session.get("user"):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return None


def require_auth_page(request: Request) -> RedirectResponse | None:
    if firstrun():
        return RedirectResponse(url="/setup", status_code=302)
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return None


# ---------------- Settings ----------------
def gettheme() -> str:
    with db() as conn:
        row = conn.execute("SELECT theme FROM settings WHERE id=1").fetchone()
    return (row["theme"] if row else "dark") or "dark"


def settheme(theme: str) -> None:
    theme = theme if theme in ("dark", "light") else "dark"
    with db() as conn:
        conn.execute(
            "UPDATE settings SET theme=?, updatedat=? WHERE id=1",
            (theme, datetime.utcnow().isoformat()),
        )


# ---------------- Widgets config ----------------
def _sanitize_widgets_list(widgets: list[str]) -> list[str]:
    allowed = set(AVAILABLETILES.keys())
    out, seen = [], set()
    for w in widgets:
        if isinstance(w, str) and w in allowed and w not in seen:
            out.append(w)
            seen.add(w)
    return out


def _sanitize_layout(layout: list[dict], widgets: list[str]) -> list[dict]:
    allowed = set(widgets)
    out: list[dict] = []
    for it in layout:
        if not isinstance(it, dict):
            continue
        key = it.get("key")
        if key not in allowed:
            continue
        try:
            x = int(it.get("x", 0))
            y = int(it.get("y", 0))
            w = int(it.get("w", 2))
            h = int(it.get("h", 1))
        except Exception:
            continue
        w = max(1, min(4, w))
        h = max(1, min(2, h))
        x = max(0, min(4 - w, x))
        y = max(0, y)
        out.append({"key": key, "x": x, "y": y, "w": w, "h": h})

    present = {x["key"] for x in out}
    y_max = 0
    for a in out:
        y_max = max(y_max, int(a["y"]) + int(a["h"]))
    for k in widgets:
        if k not in present:
            out.append({"key": k, "x": 0, "y": y_max, "w": 2, "h": 1})
            y_max += 1
    return out


def get_widgets_config() -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT widgets, layout FROM widgetsconfig WHERE id=1").fetchone()
    if not row:
        return {"widgets": DEFAULT_WIDGETS, "layout": DEFAULT_LAYOUT}

    try:
        widgets = json.loads(row["widgets"])
        layout = json.loads(row["layout"])
        if not isinstance(widgets, list):
            widgets = DEFAULT_WIDGETS
        if not isinstance(layout, list):
            layout = DEFAULT_LAYOUT
    except Exception:
        widgets, layout = DEFAULT_WIDGETS, DEFAULT_LAYOUT

    widgets = _sanitize_widgets_list(widgets)
    if not widgets:
        widgets = DEFAULT_WIDGETS
    layout = _sanitize_layout(layout, widgets)

    return {"widgets": widgets, "layout": layout}


def set_widgets_config(widgets: list[str], layout: list[dict]) -> dict[str, Any]:
    widgets = _sanitize_widgets_list(widgets)
    if not widgets:
        widgets = DEFAULT_WIDGETS
    layout = _sanitize_layout(layout, widgets)
    with db() as conn:
        conn.execute(
            "UPDATE widgetsconfig SET widgets=?, layout=?, updatedat=? WHERE id=1",
            (json.dumps(widgets), json.dumps(layout), datetime.utcnow().isoformat()),
        )
    return {"widgets": widgets, "layout": layout}


# ---------------- Jobs ----------------
def createjob(kind: str, appid: str, action: str | None = None) -> str:
    jobid = uuid.uuid4().hex
    now = datetime.utcnow().isoformat()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO jobs(id, kind, appid, action, status, createdat, startedat, finishedat, message)
            VALUES(?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (jobid, kind, appid, action, "queued", now),
        )
    return jobid


def jobsetstatus(jobid: str, status: str, message: str | None = None, started: bool = False, finished: bool = False) -> None:
    now = datetime.utcnow().isoformat()
    with db() as conn:
        if started and finished:
            conn.execute(
                """
                UPDATE jobs
                SET status=?, message=?, startedat=COALESCE(startedat, ?), finishedat=?
                WHERE id=?
                """,
                (status, message, now, now, jobid),
            )
        elif started:
            conn.execute(
                """
                UPDATE jobs
                SET status=?, message=?, startedat=COALESCE(startedat, ?)
                WHERE id=?
                """,
                (status, message, now, jobid),
            )
        elif finished:
            conn.execute(
                """
                UPDATE jobs
                SET status=?, message=?, finishedat=?
                WHERE id=?
                """,
                (status, message, now, jobid),
            )
        else:
            conn.execute("UPDATE jobs SET status=?, message=? WHERE id=?", (status, message, jobid))


def getjobs(limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, kind, appid, action, status, createdat, startedat, finishedat, message
            FROM jobs
            ORDER BY createdat DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]


async def runjobinthread(jobid: str, fn, *args):
    jobsetstatus(jobid, "running", started=True)
    try:
        ok, msg = await asyncio.to_thread(fn, *args)
        if ok:
            jobsetstatus(jobid, "success", message=msg, finished=True)
        else:
            jobsetstatus(jobid, "error", message=msg, finished=True)
    except Exception as e:
        jobsetstatus(jobid, "error", message=str(e), finished=True)


# ---------------- Metrics ----------------
def fmt_gb(x_bytes: float) -> str:
    return f"{x_bytes / (1024**3):.1f}"


def fmt_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(n)
    for u in units:
        if n < 1024 or u == units[-1]:
            if u == "B":
                return f"{n:.0f} {u}"
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d > 0:
        return f"{d}д {h:02}:{m:02}:{s:02}"
    return f"{h:02}:{m:02}:{s:02}"


def get_cpu_temp_c():
    try:
        fn = getattr(psutil, "sensors_temperatures", None)
        if fn is None:
            return None
        temps = fn()
    except Exception:
        return None
    if not temps:
        return None
    preferred = ["coretemp", "cpu_thermal", "k10temp"]
    for k in preferred:
        if k in temps and temps[k]:
            t = temps[k][0]
            return getattr(t, "current", None)
    for _, entries in temps.items():
        if entries:
            return getattr(entries[0], "current", None)
    return None


def list_all_disks():
    parts = psutil.disk_partitions(all=False)
    mountpoints, seen = [], set()
    for p in parts:
        mp = p.mountpoint
        if p.fstype in ("tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "cgroup", "cgroup2"):
            continue
        anchor = Path(mp).anchor or mp
        key = anchor.lower()
        if key in seen:
            continue
        seen.add(key)
        mountpoints.append(anchor)
    if not mountpoints:
        anchor = Path.cwd().anchor
        mountpoints = [anchor if anchor else "/"]
    return mountpoints


def tile_cpu():
    cpu = psutil.cpu_percent(interval=0.2)
    return {"id": "cpu", "title": AVAILABLETILES["cpu"], "value": f"{cpu:.0f}", "unit": "%", "sub": "Текущая нагрузка", "pct": max(0, min(100, int(cpu)))}


def tile_ram():
    mem = psutil.virtual_memory()
    return {"id": "ram", "title": AVAILABLETILES["ram"], "value": fmt_gb(mem.used), "unit": "GB", "sub": f"из {fmt_gb(mem.total)} GB", "pct": int(mem.percent)}


def tile_disk():
    disks = list_all_disks()
    lines = []
    total_used = 0
    total_all = 0
    for mp in disks:
        try:
            du = psutil.disk_usage(mp)
        except Exception:
            continue
        used = int(du.used)
        tot = int(du.total)
        pct = int(du.percent)
        total_used += used
        total_all += tot
        lines.append({"label": mp, "used_gb": fmt_gb(used), "total_gb": fmt_gb(tot), "pct": pct})

    if total_all > 0:
        overall_pct = int((total_used / total_all) * 100)
        value = fmt_gb(total_used)
        sub = f"из {fmt_gb(total_all)} GB • {len(lines)} томов"
    else:
        overall_pct = 0
        value = "—"
        sub = "нет данных"

    return {"id": "disk", "title": AVAILABLETILES["disk"], "value": value, "unit": "GB", "sub": sub, "pct": max(0, min(100, overall_pct)), "lines": lines}


def tile_temp():
    temp_c = get_cpu_temp_c()
    return {"id": "temp", "title": AVAILABLETILES["temp"], "value": "N/A" if temp_c is None else f"{temp_c:.0f}", "unit": "°C", "sub": "По данным ОС", "pct": None}


def tile_uptime():
    uptime_sec = int(time.time() - psutil.boot_time())
    return {"id": "uptime", "title": AVAILABLETILES["uptime"], "value": fmt_duration(uptime_sec), "unit": "", "sub": "С момента запуска", "pct": None}


def tile_net():
    net = psutil.net_io_counters(pernic=False)
    return {"id": "net", "title": AVAILABLETILES["net"], "value": "Трафик", "unit": "", "sub": f"↓ {fmt_bytes(net.bytes_recv)} ↑ {fmt_bytes(net.bytes_sent)}", "pct": None}


TILE_BUILDERS = {"cpu": tile_cpu, "ram": tile_ram, "disk": tile_disk, "temp": tile_temp, "uptime": tile_uptime, "net": tile_net}


def build_tiles_for_widgets(widgets: list[str]) -> list[dict]:
    tiles = []
    for wid in widgets:
        fn = TILE_BUILDERS.get(wid)
        if fn:
            tiles.append(fn())
    return tiles


# ---------------- Docker layer ----------------
def dockerclient():
    try:
        return docker.from_env()
    except DockerException:
        return None


def dockerpresent() -> bool:
    c = dockerclient()
    if not c:
        return False
    try:
        c.ping()
        return True
    except DockerException:
        return False


def appdir(appid: str) -> Path:
    return APPSDIR / appid


def labelsfor(appid: str, servicename: str) -> dict[str, str]:
    return {"serverui.managed": "true", "serverui.app": appid, "serverui.service": servicename}


def networkname(appid: str) -> str:
    return f"serverui-{appid}-net"


def ensurenetwork(client: docker.DockerClient, appid: str):
    name = networkname(appid)
    try:
        return client.networks.get(name)
    except NotFound:
        return client.networks.create(name, driver="bridge")


def ensuredirsforservice(appid: str, servicespec: dict) -> dict:
    binds = {}
    base = appdir(appid)
    base.mkdir(parents=True, exist_ok=True)
    for hostdir, containerpath in (servicespec.get("volumes") or {}).items():
        hp = base / hostdir
        hp.mkdir(parents=True, exist_ok=True)
        binds[str(hp)] = {"bind": str(containerpath), "mode": "rw"}
    for hostpath, containerpath in (servicespec.get("binds") or {}).items():
        binds[str(hostpath)] = {"bind": str(containerpath), "mode": "rw"}
    return binds


def findcontainers(client: docker.DockerClient, appid: str):
    # Важно: all=True, чтобы stopped-контейнеры не “пропадали”
    return client.containers.list(all=True, filters={"label": [f"serverui.app={appid}", "serverui.managed=true"]})


def appstatus(appid: str) -> dict:
    client = dockerclient()
    if not client:
        return {"ok": False, "error": "Docker недоступен"}
    try:
        containers = findcontainers(client, appid)
        rows = []
        running = False
        for c in containers:
            rows.append({"name": c.name, "status": c.status, "image": (c.image.tags[0] if c.image.tags else c.image.short_id)})
            if c.status == "running":
                running = True
        return {"ok": True, "containers": rows, "running": running}
    except DockerException as e:
        return {"ok": False, "error": str(e)}


def installapp(appid: str) -> tuple[bool, str]:
    meta = APPCATALOG.get(appid)
    if not meta:
        return False, "Неизвестное приложение"
    client = dockerclient()
    if not client:
        return False, "Docker недоступен"

    try:
        net = ensurenetwork(client, appid)
        services = meta.get("services") or []
        for svc in services:
            client.images.pull(svc["image"])

        for svc in services:
            svcname = svc["name"]
            containername = f"serverui-{appid}-{svcname}"
            binds = ensuredirsforservice(appid, svc)
            ports = svc.get("ports") or {}
            env = svc.get("env") or {}
            labels = labelsfor(appid, svcname)
            capadd = svc.get("cap_add")
            sysctls = svc.get("sysctls")

            try:
                existing = client.containers.get(containername)
                try:
                    net.connect(existing)
                except DockerException:
                    pass
                existing.start()
                continue
            except NotFound:
                pass

            createkwargs: dict[str, Any] = dict(
                image=svc["image"],
                name=containername,
                environment=env,
                ports=ports,
                volumes=binds,
                labels=labels,
                restart_policy={"Name": "unless-stopped"},
                detach=True,
            )
            if capadd:
                createkwargs["cap_add"] = capadd
            if sysctls:
                createkwargs["sysctls"] = sysctls

            c = client.containers.create(**createkwargs)
            net.connect(c, aliases=[svcname])
            c.start()

        return True, "OK"
    except DockerException as e:
        return False, str(e)


def actionapp(appid: str, action: str) -> tuple[bool, str]:
    client = dockerclient()
    if not client:
        return False, "Docker недоступен"
    try:
        containers = findcontainers(client, appid)

        if action == "start":
            for c in containers:
                c.start()
        elif action == "stop":
            for c in containers:
                c.stop(timeout=15)
        elif action == "restart":
            for c in containers:
                c.restart(timeout=15)
        elif action == "down":
            for c in containers:
                try:
                    if c.status == "running":
                        c.stop(timeout=15)
                except DockerException:
                    pass
                c.remove(v=False, force=True)
            try:
                net = client.networks.get(networkname(appid))
                net.remove()
            except DockerException:
                pass
        else:
            return False, "Неизвестное действие"

        return True, "OK"
    except DockerException as e:
        return False, str(e)


# ---------------- Icons (no static icons folder needed) ----------------
def _default_icon_svg(appid: str) -> str:
    letter = (appid[:1] or "A").upper()
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#6ea8fe"/><stop offset="1" stop-color="#7ee787"/></linearGradient></defs>
<rect x="8" y="8" width="80" height="80" rx="18" fill="url(#g)" opacity="0.22"/>
<rect x="8" y="8" width="80" height="80" rx="18" fill="none" stroke="rgba(255,255,255,0.25)" />
<text x="48" y="58" text-anchor="middle" font-family="ui-sans-serif,system-ui,Segoe UI,Roboto,Arial" font-size="40" font-weight="800" fill="rgba(255,255,255,0.92)">{letter}</text>
</svg>"""


def geticonmeta(appid: str) -> tuple[str, str] | None:
    with db() as conn:
        row = conn.execute("SELECT filename, mimetype FROM appicons WHERE appid=?", (appid,)).fetchone()
    if not row:
        return None
    return row["filename"], row["mimetype"]


def seticonmeta(appid: str, filename: str, mimetype: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO appicons(appid, filename, mimetype, updatedat)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(appid) DO UPDATE SET
              filename=excluded.filename,
              mimetype=excluded.mimetype,
              updatedat=excluded.updatedat
            """,
            (appid, filename, mimetype, datetime.utcnow().isoformat()),
        )


def iconurl(appid: str) -> str:
    return f"/icons/{appid}"


@app.get("/icons/{appid}")
async def getappicon(appid: str):
    meta = geticonmeta(appid)
    if not meta:
        svg = _default_icon_svg(appid)
        return Response(content=svg, media_type="image/svg+xml")

    filename, mimetype = meta
    path = ICONSDIR / filename
    if not path.exists():
        svg = _default_icon_svg(appid)
        return Response(content=svg, media_type="image/svg+xml")

    if mimetype == "image/svg+xml":
        return Response(content=path.read_text(encoding="utf-8", errors="replace"), media_type="image/svg+xml")
    return FileResponse(str(path), media_type=mimetype)


@app.post("/api/apps/icon")
async def uploadappicon(request: Request, appid: str = Form(...), file: UploadFile = File(...)):
    guard = require_auth_api(request)
    if guard:
        return guard
    if appid not in APPCATALOG:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}
    mimetype = file.content_type or ""
    ext = allowed.get(mimetype)
    if not ext:
        return JSONResponse({"ok": False, "error": "bad_type"}, status_code=400)

    safename = f"{appid}{ext}"
    outpath = ICONSDIR / safename
    data = await file.read()
    outpath.write_bytes(data)
    seticonmeta(appid, safename, mimetype)
    return {"ok": True, "icon_url": iconurl(appid)}


# ---------------- Network info ----------------
def getnetworkinfo():
    host = socket.gethostname()
    ips = []
    try:
        addrs = psutil.net_if_addrs()
        for ifname, lst in addrs.items():
            for a in lst:
                if getattr(a, "family", None) in (socket.AF_INET,):
                    if a.address and not a.address.startswith("127."):
                        ips.append({"iface": ifname, "ip": a.address})
    except Exception:
        pass
    return {"hostname": host, "ips": ips}


# ---------------- Updates (git) ----------------
def _run_git(args: list[str], cwd: Path) -> tuple[bool, str]:
    if not shutil.which("git"):
        return False, "git не найден"
    try:
        p = subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, timeout=60)
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return (p.returncode == 0), out.strip()
    except Exception as e:
        return False, str(e)


def updates_check() -> dict[str, Any]:
    root = APPDIR
    if not (root / ".git").exists():
        return {"ok": True, "supported": False, "status": "not_git_repo"}

    ok, out = _run_git(["fetch", "--all", "--prune"], root)
    if not ok:
        return {"ok": True, "supported": True, "status": "fetch_error", "log": out}

    ok, head = _run_git(["rev-parse", "HEAD"], root)
    if not ok:
        return {"ok": True, "supported": True, "status": "head_error", "log": head}

    ok, behind = _run_git(["rev-list", "HEAD..@{u}", "--count"], root)
    if not ok:
        return {"ok": True, "supported": True, "status": "no_upstream", "current": head[:12], "log": behind}

    try:
        behind_n = int((behind or "0").strip())
    except Exception:
        behind_n = 0

    return {
        "ok": True,
        "supported": True,
        "status": "ok",
        "current": head[:12],
        "behind": behind_n,
        "has_update": behind_n > 0,
    }


def updates_apply() -> dict[str, Any]:
    root = APPDIR
    if not (root / ".git").exists():
        return {"ok": True, "supported": False, "status": "not_git_repo"}

    ok, out = _run_git(["pull", "--ff-only"], root)
    if not ok:
        return {"ok": True, "supported": True, "status": "pull_error", "log": out}

    return {"ok": True, "supported": True, "status": "updated", "log": out}


# ---------------- HTML Pages ----------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if firstrun():
        return RedirectResponse(url="/setup", status_code=302)
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/home", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if firstrun():
        return RedirectResponse(url="/setup", status_code=302)
    if request.session.get("user"):
        return RedirectResponse(url="/home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "theme": gettheme(), "version": VERSION})


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if not firstrun():
        if request.session.get("user"):
            return RedirectResponse(url="/home", status_code=302)
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request, "theme": gettheme(), "version": VERSION})


@app.get("/home", response_class=HTMLResponse)
async def home_page(request: Request):
    redir = require_auth_page(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "theme": gettheme(), "version": VERSION, "active": "home", "user": request.session.get("user")},
    )


@app.get("/apps", response_class=HTMLResponse)
async def apps_page(request: Request):
    redir = require_auth_page(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        "apps.html",
        {"request": request, "theme": gettheme(), "version": VERSION, "active": "apps", "user": request.session.get("user")},
    )


@app.get("/apps/{appid}", response_class=HTMLResponse)
async def app_detail_page(request: Request, appid: str):
    redir = require_auth_page(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        "app_detail.html",
        {"request": request, "theme": gettheme(), "version": VERSION, "active": "apps", "user": request.session.get("user"), "appid": appid},
    )


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_page(request: Request):
    redir = require_auth_page(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        "jobs.html",
        {"request": request, "theme": gettheme(), "version": VERSION, "active": "jobs", "user": request.session.get("user")},
    )


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    redir = require_auth_page(request)
    if redir:
        return redir
    return templates.TemplateResponse(
        "system.html",
        {"request": request, "theme": gettheme(), "version": VERSION, "active": "system", "user": request.session.get("user")},
    )


# ---------------- API ----------------
@app.get("/api/bootstrap")
async def api_bootstrap(request: Request):
    authed = bool(request.session.get("user"))
    return {
        "ok": True,
        "first_run": firstrun(),
        "authed": authed,
        "user": request.session.get("user"),
        "theme": gettheme(),
        "version": VERSION,
        "dockerpresent": dockerpresent(),
    }


@app.post("/api/setup")
async def api_setup(request: Request, payload: dict = Body(...)):
    if not firstrun():
        return JSONResponse({"ok": False, "error": "already_setup"}, status_code=400)
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", ""))
    if len(login) < 3:
        return JSONResponse({"ok": False, "error": "login_short"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"ok": False, "error": "password_short"}, status_code=400)
    createsingleuser(login, password)
    request.session["user"] = login
    return {"ok": True}


@app.post("/api/login")
async def api_login(request: Request, payload: dict = Body(...)):
    if firstrun():
        return JSONResponse({"ok": False, "error": "first_run"}, status_code=400)
    login = str(payload.get("login", "")).strip()
    password = str(payload.get("password", ""))
    if not verifylogin(login, password):
        return JSONResponse({"ok": False, "error": "bad_credentials"}, status_code=401)
    request.session["user"] = login
    return {"ok": True}


@app.post("/api/logout")
async def api_logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.post("/api/theme")
async def api_theme(request: Request, payload: dict = Body(...)):
    guard = require_auth_api(request)
    if guard:
        return guard
    theme = str(payload.get("theme", "dark"))
    settheme(theme)
    return {"ok": True, "theme": gettheme()}


@app.get("/api/widgets/config")
async def api_widgets_get(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard
    return {"ok": True, "config": get_widgets_config()}


@app.post("/api/widgets/config")
async def api_widgets_set(request: Request, payload: dict = Body(...)):
    guard = require_auth_api(request)
    if guard:
        return guard
    widgets = payload.get("widgets")
    layout = payload.get("layout")
    if not isinstance(widgets, list) or not isinstance(layout, list):
        return JSONResponse({"ok": False, "error": "bad_payload"}, status_code=400)
    cfg = set_widgets_config(widgets, layout)
    return {"ok": True, "config": cfg}


@app.get("/api/tiles")
async def api_tiles(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard
    cfg = get_widgets_config()
    tiles = build_tiles_for_widgets(cfg["widgets"])
    return {"ok": True, "tiles": tiles}


@app.get("/api/jobs")
async def api_jobs(request: Request, limit: int = 50):
    guard = require_auth_api(request)
    if guard:
        return guard
    return {"ok": True, "jobs": getjobs(limit=int(limit))}


def _app_spec_for_ui(appid: str) -> dict[str, Any]:
    meta = APPCATALOG.get(appid) or {}
    services = meta.get("services") or []
    env: dict[str, str] = {}
    ports: list[dict[str, Any]] = []
    volumes: list[dict[str, Any]] = []

    for svc in services:
        for k, v in (svc.get("env") or {}).items():
            env[str(k)] = str(v)
        for k, v in (svc.get("ports") or {}).items():
            try:
                cport, proto = str(k).split("/")
            except ValueError:
                cport, proto = str(k), "tcp"
            ports.append({"container": int(cport), "host": int(v), "proto": proto})
        for hostdir, containerpath in (svc.get("volumes") or {}).items():
            volumes.append({"host": str(appdir(appid) / hostdir), "container": str(containerpath), "mode": "rw"})

    return {"env": env, "ports": ports, "volumes": volumes}


@app.get("/api/apps")
async def api_apps(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard

    out = []
    for appid, meta in APPCATALOG.items():
        st = appstatus(appid)
        containers = st.get("containers") or []
        installed = bool(st.get("ok") and containers)
        out.append(
            {
                "id": appid,
                "title": meta.get("title", appid),
                "desc": meta.get("description", ""),
                "tags": meta.get("tags", []),
                "installed": installed,
                "running": bool(st.get("running")),
                "url": meta.get("default_url"),
                "icon_url": iconurl(appid),
                "containers": [c.get("name") for c in containers],
            }
        )
    return {"ok": True, "apps": out}


@app.get("/api/apps/{appid}")
async def api_app_detail(request: Request, appid: str):
    guard = require_auth_api(request)
    if guard:
        return guard
    if appid not in APPCATALOG:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

    meta = APPCATALOG[appid]
    st = appstatus(appid)
    spec = _app_spec_for_ui(appid)
    containers = st.get("containers") or []
    installed = bool(st.get("ok") and containers)

    return {
        "ok": True,
        "app": {
            "id": appid,
            "title": meta.get("title", appid),
            "desc": meta.get("description", ""),
            "url": meta.get("default_url"),
            "icon_url": iconurl(appid),
            "installed": installed,
            "running": bool(st.get("running")),
            "containers": containers,
            "env": spec["env"],
            "ports": spec["ports"],
            "volumes": spec["volumes"],
        },
    }


@app.post("/api/apps/{appid}/install")
async def api_app_install(request: Request, appid: str):
    guard = require_auth_api(request)
    if guard:
        return guard
    if appid not in APPCATALOG:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    jobid = createjob("install", appid, None)
    asyncio.create_task(runjobinthread(jobid, installapp, appid))
    return {"ok": True, "jobid": jobid}


@app.post("/api/apps/{appid}/action")
async def api_app_action(request: Request, appid: str, payload: dict = Body(...)):
    guard = require_auth_api(request)
    if guard:
        return guard
    if appid not in APPCATALOG:
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    action = str(payload.get("action", "")).strip()
    if action not in ("start", "stop", "restart", "down"):
        return JSONResponse({"ok": False, "error": "bad_action"}, status_code=400)
    jobid = createjob("action", appid, action)
    asyncio.create_task(runjobinthread(jobid, actionapp, appid, action))
    return {"ok": True, "jobid": jobid}


@app.get("/api/apps/{appid}/logs")
async def api_app_logs(
    request: Request,
    appid: str,
    container: str = Query(...),
    tail: int = Query(400, ge=1, le=5000),
):
    guard = require_auth_api(request)
    if guard:
        return guard

    client = dockerclient()
    if not client:
        return JSONResponse({"ok": False, "error": "docker_unavailable"}, status_code=503)

    try:
        cont = client.containers.get(container)
        raw = cont.logs(tail=int(tail))
        return {"ok": True, "text": raw.decode("utf-8", errors="replace")}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/system/info")
async def api_system_info(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard
    info = {
        "version": VERSION,
        "python": platform.python_version(),
        "os": platform.platform(),
        "arch": platform.machine(),
    }
    return {"ok": True, "info": info, "net": getnetworkinfo(), "dockerpresent": dockerpresent()}


@app.post("/api/system/password")
async def api_system_password(request: Request, payload: dict = Body(...)):
    guard = require_auth_api(request)
    if guard:
        return guard
    current = str(payload.get("current_password", ""))
    new = str(payload.get("new_password", ""))

    if len(new) < 6:
        return JSONResponse({"ok": False, "error": "password_short"}, status_code=400)

    u = getsingleuser()
    if not u:
        return JSONResponse({"ok": False, "error": "no_user"}, status_code=400)
    if not verifylogin(u["username"], current):
        return JSONResponse({"ok": False, "error": "bad_current_password"}, status_code=401)

    setpassword(new)
    return {"ok": True}


@app.get("/api/system/backup")
async def api_system_backup(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard

    DATADIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        backupdb = td / "app.db"

        src = sqlite3.connect(DBPATH)
        dst = sqlite3.connect(backupdb)
        src.backup(dst)
        dst.close()
        src.close()

        zippath = td / "backup.zip"
        with zipfile.ZipFile(zippath, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(backupdb, arcname="app.db")
            if APPSDIR.exists():
                for p in APPSDIR.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(DATADIR)
                        z.write(p, arcname=str(rel))

        return FileResponse(
            str(zippath),
            media_type="application/zip",
            filename=f"server-ui-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip",
        )


@app.get("/api/system/update/check")
async def api_update_check(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard
    return updates_check()


@app.post("/api/system/update/apply")
async def api_update_apply(request: Request):
    guard = require_auth_api(request)
    if guard:
        return guard
    return updates_apply()


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"
