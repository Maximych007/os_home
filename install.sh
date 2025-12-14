#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/Maximych007/os_home.git"
INSTALL_DIR="/opt/os_home"

if [ "$(id -u)" -ne 0 ]; then
  echo "Запусти: sudo bash install.sh"
  exit 1
fi

echo "[1/5] Пакеты..."
apt-get update
apt-get install -y --no-install-recommends git curl ca-certificates
apt-get clean
rm -rf /var/lib/apt/lists/*

echo "[2/5] Docker (если нет)..."
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sh /tmp/get-docker.sh
  rm -f /tmp/get-docker.sh
fi

echo "[3/5] Клонирование/обновление..."
if [ -d "${INSTALL_DIR}/.git" ]; then
  git -C "${INSTALL_DIR}" pull --ff-only
else
  rm -rf "${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

echo "[4/5] Секрет в .env..."
mkdir -p "${INSTALL_DIR}"
ENV_FILE="${INSTALL_DIR}/.env"
if [ ! -f "${ENV_FILE}" ] || ! grep -q "^SERVER_UI_SECRET=" "${ENV_FILE}"; then
  SECRET="$(head -c 48 /dev/urandom | base64 | tr -d '\n' | cut -c1-48)"
  echo "SERVER_UI_SECRET=${SECRET}" > "${ENV_FILE}"
fi

echo "[5/5] Запуск docker compose..."
cd "${INSTALL_DIR}"
docker compose up -d --build
echo "Готово: открой http://<IP_малинки>:8000"
