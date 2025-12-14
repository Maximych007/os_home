import os
import sqlite3
import time
import json
import socket
import platform
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import bcrypt
import psutil
import docker
from docker.errors import DockerException, NotFound

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    FileResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


VERSION = "0.5.0"

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
APPS_DIR = DATA_DIR / "apps"
ICONS_DIR = DATA_DIR / "icons"

SESSION_SECRET = os.environ.get("SERVER_UI_SECRET", "dev-secret-change-me")

DEFAULT_TILES_ORDER = ["cpu", "ram", "disk", "temp", "uptime", "net"]
AVAILABLE_TILES = {
    "cpu": "CPU",
    "ram": "RAM",
    "disk": "Хранилище",
    "temp": "Температура",
    "uptime": "Аптайм",
    "net": "Сеть",
}

# ---- Каталог приложений (через Docker Engine API) ----
# volumes: host_dir (relative to APPS_DIR/app_id) -> container_path
# ports: container_port/proto -> host_port
APP_CATALOG = {
    "jellyfin": {
        "title": "Jellyfin",
        "description": "Медиасервер",
        "default_url": "http://localhost:8096",
        "services": [
            {
                "name": "jellyfin",
                "image": "jellyfin/jellyfin:latest",
                "env": {},
                "ports": {"8096/tcp": 8096},
                "volumes": {"config": "/config", "cache": "/cache", "media": "/media"},
            }
        ],
    },

    "nextcloud": {
        "title": "Nextcloud",
        "description": "Файловое облако (PostgreSQL + Nextcloud)",
        "default_url": "http://localhost:8080",
        "services": [
            {
                "name": "db",
                "image": "postgres:16",
                "env": {
                    "POSTGRES_PASSWORD": "nextcloud",
                    "POSTGRES_USER": "nextcloud",
                    "POSTGRES_DB": "nextcloud",
                },
                "ports": {},
                "volumes": {"db": "/var/lib/postgresql/data"},
            },
            {
                "name": "app",
                "image": "nextcloud:29",
                "env": {
                    "POSTGRES_HOST": "db",
                    "POSTGRES_PASSWORD": "nextcloud",
                    "POSTGRES_USER": "nextcloud",
                    "POSTGRES_DB": "nextcloud",
                },
                "ports": {"80/tcp": 8080},
                "volumes": {"nc": "/var/www/html"},
            },
        ],
    },

    "portainer": {
        "title": "Portainer CE",
        "description": "Графический интерфейс для Docker",
        "default_url": "http://localhost:9000",
        "services": [
            {
                "name": "portainer",
                "image": "portainer/portainer-ce:latest",
                "env": {},
                "ports": {"9000/tcp": 9000},
                "volumes": {"data": "/data"},
                "binds": {"/var/run/docker.sock": "/var/run/docker.sock"},
            }
        ],
    },

    "homeassistant": {
        "title": "Home Assistant",
        "description": "Умный дом (веб‑интерфейс обычно на :8123)",
        "default_url": "http://localhost:8123",
        "services": [
            {
                "name": "homeassistant",
                "image": "ghcr.io/home-assistant/home-assistant:stable",
                "env": {"TZ": "Asia/Yekaterinburg"},
                "ports": {"8123/tcp": 8123},
                "volumes": {"config": "/config"},
            }
        ],
    },

    "syncthing": {
        "title": "Syncthing",
        "description": "Синхронизация файлов между устройствами",
        "default_url": "http://localhost:8384",
        "services": [
            {
                "name": "syncthing",
                "image": "syncthing/syncthing:latest",
                "env": {},
                "ports": {
                    "8384/tcp": 8384,      # GUI
                    "22000/tcp": 22000,
                    "22000/udp": 22000,
                    "21027/udp": 21027,
                },
                "volumes": {"data": "/var/syncthing"},
            }
        ],
    },

    "grafana_prometheus": {
        "title": "Grafana + Prometheus",
        "description": "Мониторинг (Grafana на :3000, Prometheus на :9090)",
        "default_url": "http://localhost:3000",
        "services": [
            {
                "name": "prometheus",
                "image": "prom/prometheus:latest",
                "env": {},
                "ports": {"9090/tcp": 9090},
                "volumes": {"prom": "/prometheus"},
            },
            {
                "name": "grafana",
                "image": "grafana/grafana:latest",
                "env": {},
                "ports": {"3000/tcp": 3000},
                "volumes": {"grafana": "/var/lib/grafana"},
            },
        ],
    },

    "nodered": {
        "title": "Node-RED",
        "description": "Автоматизация и интеграции",
        "default_url": "http://localhost:1880",
        "services": [
            {
                "name": "nodered",
                "image": "nodered/node-red:latest",
                "env": {},
                "ports": {"1880/tcp": 1880},
                "volumes": {"data": "/data"},
            }
        ],
    },

    "code_server": {
        "title": "code-server",
        "description": "VS Code в браузере",
        "default_url": "http://localhost:8081",
        "services": [
            {
                "name": "code",
                "image": "codercom/code-server:latest",
                "env": {"PASSWORD": "change-me"},
                "ports": {"8080/tcp": 8081},
                "volumes": {"project": "/home/coder/project"},
            }
        ],
    },
}


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


# ---------------- DB ----------------
def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ui_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                tiles_order TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                theme TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_icons (
                app_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        cur = conn.execute("SELECT id FROM ui_config WHERE id=1")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO ui_config (id, tiles_order, updated_at) VALUES (1, ?, ?)",
                (json.dumps(DEFAULT_TILES_ORDER), datetime.utcnow().isoformat()),
            )

        cur = conn.execute("SELECT id FROM settings WHERE id=1")
        if cur.fetchone() is None:
            conn.execute(
                "INSERT INTO settings (id, theme, updated_at) VALUES (1, ?, ?)",
                ("dark", datetime.utcnow().isoformat()),
            )


def get_single_user():
    with db() as conn:
        return conn.execute(
            "SELECT id, username, password_hash, created_at FROM users WHERE id = 1"
        ).fetchone()


def create_single_user(username: str, password: str) -> None:
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with db() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, created_at) VALUES (1, ?, ?, ?)",
            (username, pw_hash, datetime.utcnow().isoformat()),
        )


def set_password(new_password: str) -> None:
    pw_hash = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    with db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=1", (pw_hash,))


def verify_login(username: str, password: str) -> bool:
    u = get_single_user()
    if not u:
        return False
    if u["username"] != username:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), u["password_hash"].encode("utf-8"))


def get_tiles_order() -> list[str]:
    with db() as conn:
        row = conn.execute("SELECT tiles_order FROM ui_config WHERE id=1").fetchone()
        if not row:
            return DEFAULT_TILES_ORDER[:]
        try:
            order = json.loads(row["tiles_order"])
            if not isinstance(order, list):
                return DEFAULT_TILES_ORDER[:]
            out, seen = [], set()
            for x in order:
                if isinstance(x, str) and x in AVAILABLE_TILES and x not in seen:
                    out.append(x)
                    seen.add(x)
            return out if out else DEFAULT_TILES_ORDER[:]
        except Exception:
            return DEFAULT_TILES_ORDER[:]


def set_tiles_order(order: list[str]) -> None:
    out, seen = [], set()
    for x in order:
        if isinstance(x, str) and x in AVAILABLE_TILES and x not in seen:
            out.append(x)
            seen.add(x)
    if not out:
        out = DEFAULT_TILES_ORDER[:]
    with db() as conn:
        conn.execute(
            "UPDATE ui_config SET tiles_order=?, updated_at=? WHERE id=1",
            (json.dumps(out), datetime.utcnow().isoformat()),
        )


def get_theme() -> str:
    with db() as conn:
        row = conn.execute("SELECT theme FROM settings WHERE id=1").fetchone()
        return (row["theme"] if row else "dark") or "dark"


def set_theme(theme: str) -> None:
    theme = theme if theme in ("dark", "light") else "dark"
    with db() as conn:
        conn.execute(
            "UPDATE settings SET theme=?, updated_at=? WHERE id=1",
            (theme, datetime.utcnow().isoformat()),
        )


@app.on_event("startup")
def _startup():
    init_db()
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    ICONS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------- Auth helpers ----------------
def first_run() -> bool:
    return get_single_user() is None


def require_auth(request: Request):
    if not request.session.get("user"):
        return RedirectResponse(url="/login", status_code=302)
    return None


# ---------------- Formatting helpers ----------------
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


def fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d > 0:
        return f"{d}д {h:02}:{m:02}:{s:02}"
    return f"{h:02}:{m:02}:{s:02}"


# ---------------- Hardware / metrics ----------------
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
        if p.fstype in (
            "tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "cgroup", "cgroup2"
        ):
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
    return {"id": "cpu", "title": AVAILABLE_TILES["cpu"], "value": f"{cpu:.0f}", "unit": "%", "sub": "Текущая нагрузка", "pct": max(0, min(100, int(cpu)))}


def tile_ram():
    mem = psutil.virtual_memory()
    return {"id": "ram", "title": AVAILABLE_TILES["ram"], "value": fmt_gb(mem.used), "unit": "GB", "sub": f"из {fmt_gb(mem.total)} GB", "pct": int(mem.percent)}


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

    return {"id": "disk", "title": AVAILABLE_TILES["disk"], "value": value, "unit": "GB", "sub": sub, "pct": max(0, min(100, overall_pct)), "lines": lines}


def tile_temp():
    temp_c = get_cpu_temp_c()
    return {"id": "temp", "title": AVAILABLE_TILES["temp"], "value": "N/A" if temp_c is None else f"{temp_c:.0f}", "unit": "°C", "sub": "По данным ОС", "pct": None}


def tile_uptime():
    uptime_sec = int(time.time() - psutil.boot_time())
    return {"id": "uptime", "title": AVAILABLE_TILES["uptime"], "value": fmt_duration(uptime_sec), "unit": "", "sub": "С момента запуска", "pct": None}


def tile_net():
    net = psutil.net_io_counters(pernic=False)
    return {"id": "net", "title": AVAILABLE_TILES["net"], "value": "Трафик", "unit": "", "sub": f"↓ {fmt_bytes(net.bytes_recv)}  ↑ {fmt_bytes(net.bytes_sent)}", "pct": None}


TILE_BUILDERS = {"cpu": tile_cpu, "ram": tile_ram, "disk": tile_disk, "temp": tile_temp, "uptime": tile_uptime, "net": tile_net}


def build_tiles(order: list[str] | None = None):
    if order is None:
        order = get_tiles_order()
    tiles = []
    for tid in order:
        fn = TILE_BUILDERS.get(tid)
        if fn:
            tiles.append(fn())
    return tiles


# ---------------- Docker Engine API layer ----------------
def docker_client():
    try:
        return docker.from_env()
    except DockerException:
        return None


def docker_present() -> bool:
    c = docker_client()
    if not c:
        return False
    try:
        _ = c.ping()
        return True
    except DockerException:
        return False


def app_dir(app_id: str) -> Path:
    return APPS_DIR / app_id


def labels_for(app_id: str, service_name: str):
    return {
        "serverui.managed": "true",
        "serverui.app": app_id,
        "serverui.service": service_name,
    }


def network_name(app_id: str) -> str:
    return f"serverui_{app_id}_net"


def ensure_network(client: docker.DockerClient, app_id: str):
    name = network_name(app_id)
    try:
        return client.networks.get(name)
    except NotFound:
        return client.networks.create(name, driver="bridge")


def ensure_dirs_for_service(app_id: str, service_spec: dict) -> dict:
    binds = {}
    base = app_dir(app_id)
    base.mkdir(parents=True, exist_ok=True)

    for host_dir, container_path in (service_spec.get("volumes") or {}).items():
        hp = base / host_dir
        hp.mkdir(parents=True, exist_ok=True)
        binds[str(hp)] = {"bind": container_path, "mode": "rw"}

    for host_path, container_path in (service_spec.get("binds") or {}).items():
        binds[str(host_path)] = {"bind": container_path, "mode": "rw"}

    return binds


def find_containers(client: docker.DockerClient, app_id: str):
    return client.containers.list(all=True, filters={"label": [f"serverui.app={app_id}", "serverui.managed=true"]})


def app_status(app_id: str) -> dict:
    client = docker_client()
    if not client:
        return {"ok": False, "error": "Docker недоступен"}

    try:
        containers = find_containers(client, app_id)
        rows = []
        running = False
        for c in containers:
            rows.append({
                "name": c.name,
                "status": c.status,
                "image": (c.image.tags[0] if c.image.tags else c.image.short_id),
            })
            if c.status == "running":
                running = True
        return {"ok": True, "containers": rows, "running": running}
    except DockerException as e:
        return {"ok": False, "error": str(e)}


def install_app(app_id: str) -> tuple[bool, str]:
    meta = APP_CATALOG.get(app_id)
    if not meta:
        return False, "Неизвестное приложение"

    client = docker_client()
    if not client:
        return False, "Docker недоступен"

    try:
        net = ensure_network(client, app_id)

        for svc in meta["services"]:
            client.images.pull(svc["image"])

        for svc in meta["services"]:
            svc_name = svc["name"]
            container_name = f"serverui-{app_id}-{svc_name}"

            binds = ensure_dirs_for_service(app_id, svc)
            ports = svc.get("ports") or {}
            env = svc.get("env") or {}
            labels = labels_for(app_id, svc_name)

            try:
                existing = client.containers.get(container_name)
                try:
                    net.connect(existing)
                except DockerException:
                    pass
                existing.start()
                continue
            except NotFound:
                pass

            c = client.containers.create(
                image=svc["image"],
                name=container_name,
                environment=env,
                ports=ports,
                volumes=binds,
                labels=labels,
                restart_policy={"Name": "unless-stopped"},
                detach=True,
            )
            net.connect(c, aliases=[svc_name])
            c.start()

        return True, "Установлено"
    except DockerException as e:
        return False, str(e)


def action_app(app_id: str, action: str) -> tuple[bool, str]:
    client = docker_client()
    if not client:
        return False, "Docker недоступен"

    try:
        containers = find_containers(client, app_id)

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
                net = client.networks.get(network_name(app_id))
                net.remove()
            except DockerException:
                pass
        else:
            return False, "Неизвестное действие"

        return True, "OK"
    except DockerException as e:
        return False, str(e)


# ---------------- Icons helpers ----------------
def get_icon_filename(app_id: str) -> str | None:
    with db() as conn:
        row = conn.execute("SELECT filename FROM app_icons WHERE app_id=?", (app_id,)).fetchone()
        return row["filename"] if row else None


def set_icon_filename(app_id: str, filename: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO app_icons(app_id, filename, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET filename=excluded.filename, updated_at=excluded.updated_at",
            (app_id, filename, datetime.utcnow().isoformat()),
        )


def icon_url(app_id: str) -> str:
    return f"/icons/{app_id}"


# ---------------- Network info ----------------
def get_network_info():
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


# ---------------- Routes ----------------
@app.get("/", response_class=HTMLResponse)
async def index():
    return RedirectResponse(url="/setup" if first_run() else "/login", status_code=302)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    if not first_run():
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "setup.html",
        {"request": request, "first_run": True, "theme": get_theme(), "version": VERSION, "authed": False},
    )


@app.post("/setup")
async def setup_create_admin(
    request: Request,
    admin_login: str = Form(...),
    admin_password: str = Form(...),
):
    if not first_run():
        return RedirectResponse(url="/login", status_code=302)

    admin_login = admin_login.strip()
    if len(admin_login) < 3:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "first_run": True, "theme": get_theme(), "version": VERSION, "authed": False,
             "error": "Логин слишком короткий (минимум 3 символа)."},
            status_code=400,
        )
    if len(admin_password) < 6:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "first_run": True, "theme": get_theme(), "version": VERSION, "authed": False,
             "error": "Пароль слишком короткий (минимум 6 символов)."},
            status_code=400,
        )

    create_single_user(admin_login, admin_password)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if first_run():
        return RedirectResponse(url="/setup", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "first_run": False, "theme": get_theme(), "version": VERSION, "authed": False},
    )


@app.post("/login")
async def login_post(request: Request, login: str = Form(...), password: str = Form(...)):
    if first_run():
        return RedirectResponse(url="/setup", status_code=302)

    if not verify_login(login.strip(), password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "first_run": False, "theme": get_theme(), "version": VERSION, "authed": False,
             "error": "Неверный логин или пароль."},
            status_code=401,
        )

    request.session["user"] = login.strip()
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    redir = require_auth(request)
    if redir:
        return redir

    order = get_tiles_order()
    tiles = build_tiles(order)
    available = [{"id": k, "title": AVAILABLE_TILES[k]} for k in AVAILABLE_TILES.keys()]

    installed_cards = []
    for app_id, meta in APP_CATALOG.items():
        st = app_status(app_id)
        is_installed = bool(st.get("ok") and st.get("containers"))
        if is_installed:
            installed_cards.append({
                "id": app_id,
                "title": meta.get("title", app_id),
                "url": meta.get("default_url", ""),
                "status": st,
                "icon_url": icon_url(app_id),
            })

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "tiles": tiles,
            "tiles_order": order,
            "available_tiles": available,
            "installed_apps_cards": installed_cards,
            "docker_present": docker_present(),
            "theme": get_theme(),
            "version": VERSION,
            "authed": True,
        },
    )


@app.get("/api/tiles")
async def api_tiles(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"ok": False, "redirect": "/login"}, status_code=401)
    return {"ok": True, "tiles": build_tiles()}


@app.get("/api/tiles/config")
async def api_tiles_config(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"ok": False, "redirect": "/login"}, status_code=401)
    return {
        "ok": True,
        "order": get_tiles_order(),
        "available": [{"id": k, "title": AVAILABLE_TILES[k]} for k in AVAILABLE_TILES.keys()],
    }


@app.post("/api/tiles/config")
async def api_tiles_config_set(request: Request):
    redir = require_auth(request)
    if redir:
        return JSONResponse({"ok": False, "redirect": "/login"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Неверный JSON"}, status_code=400)

    order = payload.get("order")
    if not isinstance(order, list):
        return JSONResponse({"ok": False, "error": "order должен быть списком"}, status_code=400)

    set_tiles_order(order)
    return {"ok": True, "order": get_tiles_order()}


# -------- System --------
@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    redir = require_auth(request)
    if redir:
        return redir

    u = get_single_user()
    net = get_network_info()
    info = {
        "version": VERSION,
        "python": platform.python_version(),
        "os": platform.platform(),
        "arch": platform.machine(),
    }
    return templates.TemplateResponse(
        "system.html",
        {
            "request": request,
            "theme": get_theme(),
            "version": VERSION,
            "authed": True,
            "user": (u["username"] if u else ""),
            "net": net,
            "info": info,
            "docker_present": docker_present(),
        },
    )


@app.post("/system/theme")
async def system_set_theme(request: Request, theme: str = Form(...)):
    redir = require_auth(request)
    if redir:
        return redir
    set_theme(theme)
    return RedirectResponse(url="/system", status_code=302)


@app.post("/system/password")
async def system_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    redir = require_auth(request)
    if redir:
        return redir

    u = get_single_user()
    if not u:
        return RedirectResponse(url="/setup", status_code=302)

    if len(new_password) < 6:
        info = {
            "version": VERSION,
            "python": platform.python_version(),
            "os": platform.platform(),
            "arch": platform.machine(),
        }
        return templates.TemplateResponse(
            "system.html",
            {
                "request": request,
                "theme": get_theme(),
                "version": VERSION,
                "authed": True,
                "user": u["username"],
                "net": get_network_info(),
                "info": info,
                "docker_present": docker_present(),
                "error": "Новый пароль слишком короткий (минимум 6 символов).",
            },
            status_code=400,
        )

    if not verify_login(u["username"], current_password):
        info = {
            "version": VERSION,
            "python": platform.python_version(),
            "os": platform.platform(),
            "arch": platform.machine(),
        }
        return templates.TemplateResponse(
            "system.html",
            {
                "request": request,
                "theme": get_theme(),
                "version": VERSION,
                "authed": True,
                "user": u["username"],
                "net": get_network_info(),
                "info": info,
                "docker_present": docker_present(),
                "error": "Текущий пароль неверный.",
            },
            status_code=401,
        )

    set_password(new_password)
    return RedirectResponse(url="/system", status_code=302)


@app.get("/system/backup")
async def system_backup(request: Request):
    redir = require_auth(request)
    if redir:
        return redir

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        backup_db = td / "app.db"

        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(backup_db)
        src.backup(dst)
        dst.close()
        src.close()

        zip_path = td / "backup.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(backup_db, arcname="app.db")
            if APPS_DIR.exists():
                for p in APPS_DIR.rglob("*"):
                    if p.is_file():
                        rel = p.relative_to(DATA_DIR)
                        z.write(p, arcname=str(rel))

        return FileResponse(
            str(zip_path),
            media_type="application/zip",
            filename=f"server-ui-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip",
        )


# -------- Apps --------
@app.get("/apps", response_class=HTMLResponse)
async def apps_page(request: Request):
    redir = require_auth(request)
    if redir:
        return redir

    installed_cards = []
    for app_id, meta in APP_CATALOG.items():
        st = app_status(app_id)
        is_installed = bool(st.get("ok") and st.get("containers"))
        if is_installed:
            installed_cards.append({
                "id": app_id,
                "title": meta.get("title", app_id),
                "url": meta.get("default_url", ""),
                "status": st,
                "icon_url": icon_url(app_id),
            })

    catalog_cards = []
    for app_id, meta in APP_CATALOG.items():
        st = app_status(app_id)
        is_installed = bool(st.get("ok") and st.get("containers"))
        catalog_cards.append({
            "id": app_id,
            "title": meta["title"],
            "description": meta["description"],
            "installed": is_installed,
            "icon_url": icon_url(app_id),
        })

    return templates.TemplateResponse(
        "apps.html",
        {
            "request": request,
            "theme": get_theme(),
            "version": VERSION,
            "authed": True,
            "docker_present": docker_present(),
            "installed": installed_cards,
            "catalog": catalog_cards,
        },
    )


@app.post("/apps/install")
async def apps_install(request: Request, app_id: str = Form(...)):
    redir = require_auth(request)
    if redir:
        return redir

    install_app(app_id)
    return RedirectResponse(url="/apps", status_code=302)


@app.post("/apps/action")
async def apps_action(request: Request, app_id: str = Form(...), action: str = Form(...)):
    redir = require_auth(request)
    if redir:
        return redir

    action_app(app_id, action)
    return RedirectResponse(url="/apps", status_code=302)


@app.post("/apps/icon")
async def upload_app_icon(
    request: Request,
    app_id: str = Form(...),
    file: UploadFile = File(...),
):
    redir = require_auth(request)
    if redir:
        return redir

    if app_id not in APP_CATALOG:
        return RedirectResponse(url="/apps", status_code=302)

    allowed = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    ext = allowed.get(file.content_type or "")
    if not ext:
        return RedirectResponse(url="/apps", status_code=302)

    safe_name = f"{app_id}{ext}"
    out_path = ICONS_DIR / safe_name
    data = await file.read()
    out_path.write_bytes(data)
    set_icon_filename(app_id, safe_name)

    return RedirectResponse(url="/apps", status_code=302)


@app.get("/icons/{app_id}")
async def get_app_icon(app_id: str):
    default_path = APP_DIR / "static" / "icons" / "default.png"
    fn = get_icon_filename(app_id)
    if not fn:
        return FileResponse(str(default_path))
    path = ICONS_DIR / fn
    if not path.exists():
        return FileResponse(str(default_path))
    return FileResponse(str(path))


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    path = APP_DIR / "static" / "favicon.ico"
    if path.exists():
        return FileResponse(str(path))
    return RedirectResponse(url="/static/favicon.ico", status_code=302)

# ======= Updates (версия системы и проверка/обновления) =======
from fastapi import Depends
from fastapi.responses import HTMLResponse
from pathlib import Path
import json, subprocess

STATE_FILE = Path("/var/lib/os_home/update.json")
TRIGGER_FILE = Path("/var/lib/os_home/trigger")

def get_current_version() -> str:
    # Версия панели из константы VERSION в этом файле
    try:
        return VERSION
    except Exception:
        return "unknown"

def read_update_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"checked_at": "", "local_version": get_current_version(),
            "remote_version": "", "os_version": "", "update_available": False}

@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    redir = require_auth(request)
    if redir: return redir
    u = get_single_user()
    info = {
        "version": get_current_version(),
        "python": platform.python_version(),
        "os": platform.platform(),
        "arch": platform.machine(),
    }
    state = read_update_state()
    return templates.TemplateResponse("system.html", {
        "request": request,
        "theme": get_theme(),
        "version": VERSION,
        "authed": True,
        "user": (u["username"] if u else ""),
        "net": get_network_info(),
        "info": info,
        "docker_present": docker_present(),
        "upd": state
    })

@app.post("/system/check")
async def system_check(request: Request):
    redir = require_auth(request)
    if redir: return JSONResponse({"ok": False, "redirect": "/login"}, status_code=401)
    TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Пишем триггер "check" — systemd path-юнит запустит сервис
    TRIGGER_FILE.write_text("check\n", encoding="utf-8")
    return {"ok": True, "message": "Проверка запущена"}

@app.post("/system/update")
async def system_update(request: Request):
    redir = require_auth(request)
    if redir: return JSONResponse({"ok": False, "redirect": "/login"}, status_code=401)
    TRIGGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Пишем триггер "apply" — service выполнит pull + compose up -d --build
    TRIGGER_FILE.write_text("apply\n", encoding="utf-8")
    return {"ok": True, "message": "Обновление запущено"}
