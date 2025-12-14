#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Maximych007/os_home.git"
INSTALL_DIR="/opt/os_home"
STATE_DIR="/var/lib/os_home"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти: sudo bash install.sh"
  exit 1
fi

echo "[1/6] Пакеты..."
apt-get update
apt-get install -y --no-install-recommends git curl ca-certificates
apt-get clean
rm -rf /var/lib/apt/lists/*

echo "[2/6] Docker (если нет)..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
fi

echo "[3/6] Клонирование/обновление..."
if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  rm -rf "${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "[4/6] Секрет в .env..."
mkdir -p "${INSTALL_DIR}"
ENV_FILE="${INSTALL_DIR}/.env"
if [ ! -f "${ENV_FILE}" ] || ! grep -q "^SERVER_UI_SECRET=" "${ENV_FILE}"; then
  SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '\n' | cut -c1-48)"
  echo "SERVER_UI_SECRET=${SECRET}" > "${ENV_FILE}"
fi

echo "[5/6] Установка авто-обновлений..."
mkdir -p "${STATE_DIR}"

# Скрипт обновления
install -m 0755 /dev/stdin /usr/local/bin/os_home_update.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="/opt/os_home"
STATE_DIR="/var/lib/os_home"
STATE_FILE="${STATE_DIR}/update.json"
TRIGGER_FILE="${STATE_DIR}/trigger"
OS_RELEASE="/etc/os-release"

read_versions() {
  local local_v remote_v os_v
  if [[ -f "${REPO_DIR}/VERSION" ]]; then
    local_v="$(tr -d '\n' < "${REPO_DIR}/VERSION" || true)"
  else
    local_v="$(sed -n 's/.*VERSION *= *"\(.*\)".*/\1/p' "${REPO_DIR}/app/main.py" | head -n1 || true)"
  fi
  git -C "${REPO_DIR}" fetch origin main --prune >/dev/null 2>&1 || true
  if [[ -f "${REPO_DIR}/VERSION" ]]; then
    remote_v="$(git -C "${REPO_DIR}" show origin/main:VERSION 2>/dev/null || true)"
  else
    remote_v="$(git -C "${REPO_DIR}" show origin/main:app/main.py 2>/dev/null | sed -n 's/.*VERSION *= *"\(.*\)".*/\1/p' | head -n1 || true)"
  fi
  if [[ -r "${OS_RELEASE}" ]]; then
    os_v="$(. "${OS_RELEASE}"; echo "${PRETTY_NAME:-unknown}")"
  else
    os_v="$(uname -a)"
  fi
  printf '%s %s %s\n' "$local_v" "$remote_v" "$os_v"
}

write_state() {
  mkdir -p "${STATE_DIR}"
  cat > "${STATE_FILE}" <<JSON
{"checked_at":"$(date -Is)","local_version":"$1","remote_version":"$2","os_version":"$3","update_available":$( [[ -n "$2" && "$1" != "$2" ]] && echo true || echo false )}
JSON
}

apply_update() {
  git -C "${REPO_DIR}" pull --ff-only
  (cd "${REPO_DIR}" && docker compose up -d --build)
}

cmd="${1:-auto}"
case "$cmd" in
  check)
    read lv rv osv < <(read_versions)
    write_state "$lv" "$rv" "$osv"
    ;;
  apply)
    read lv rv osv < <(read_versions)
    if [[ -n "$rv" && "$lv" != "$rv" ]]; then
      apply_update
      read lv rv osv < <(read_versions)
    fi
    write_state "$lv" "$rv" "$osv"
    ;;
  auto|*)
    if [[ -s "${TRIGGER_FILE}" ]]; then
      action="$(tr -d '\n\r\t ' < "${TRIGGER_FILE}" || true)"
      : > "${TRIGGER_FILE}"
      if [[ "$action" == "apply" ]]; then
        exec "$0" apply
      fi
    fi
    exec "$0" check
    ;;
esac
SH

# systemd units
cat >/etc/systemd/system/os-home-update.service <<'UNIT'
[Unit]
Description=os_home: check/apply update
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/os_home_update.sh auto
UNIT

cat >/etc/systemd/system/os-home-update.timer <<'UNIT'
[Unit]
Description=os_home: periodic update check

[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat >/etc/systemd/system/os-home-update.path <<'UNIT'
[Unit]
Description=os_home: react to update trigger file

[Path]
PathChanged=/var/lib/os_home/trigger
PathModified=/var/lib/os_home/trigger
Unit=os-home-update.service

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now os-home-update.timer
systemctl enable --now os-home-update.path

echo "[6/6] Запуск docker compose..."
cd "${INSTALL_DIR}"
docker compose up -d --build
echo "Готово: открой http://<IP>:8000"
