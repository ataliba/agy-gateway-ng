#!/usr/bin/env bash
# agy-gateway-ng — instalador de LXC no Proxmox VE (instalação nativa: venv + systemd)
#
# Roda no shell do HOST Proxmox (não dentro de um container):
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/ataliba/agy-gateway-ng/main/ct/agy-gateway-ng.sh)"
#
# Variáveis de ambiente pra customizar (opcionais, têm default sensato):
#   CTID, HOSTNAME, STORAGE, TEMPLATE_STORAGE, DISK_GB, CORES, MEMORY_MB,
#   NET_CONFIG, REPO_URL, GATEWAY_PORT

set -euo pipefail

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
HOSTNAME="${HOSTNAME:-agy-gateway-ng}"
STORAGE="${STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
DISK_GB="${DISK_GB:-4}"
CORES="${CORES:-1}"
MEMORY_MB="${MEMORY_MB:-512}"
NET_CONFIG="${NET_CONFIG:-name=eth0,bridge=vmbr0,ip=dhcp}"
REPO_URL="${REPO_URL:-https://github.com/ataliba/agy-gateway-ng.git}"
GATEWAY_PORT="${GATEWAY_PORT:-8000}"

command -v pct >/dev/null 2>&1 || { echo "Rode isso no host Proxmox VE (comando 'pct' não encontrado)." >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Precisa rodar como root." >&2; exit 1; }

echo "==> Resolvendo template Debian 12 mais recente"
TEMPLATE=$(pveam available --section system 2>/dev/null | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)
if [[ -z "${TEMPLATE}" ]]; then
  pveam update
  TEMPLATE=$(pveam available --section system | awk '/debian-12-standard/{print $2}' | sort -V | tail -1)
fi
[[ -n "${TEMPLATE}" ]] || { echo "Não achei template debian-12-standard em 'pveam available'." >&2; exit 1; }

if ! pveam list "${TEMPLATE_STORAGE}" 2>/dev/null | grep -q "${TEMPLATE}"; then
  echo "==> Baixando template ${TEMPLATE}"
  pveam download "${TEMPLATE_STORAGE}" "${TEMPLATE}"
fi

echo "==> Criando LXC ${CTID} (${HOSTNAME})"
pct create "${CTID}" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "${HOSTNAME}" \
  --cores "${CORES}" \
  --memory "${MEMORY_MB}" \
  --swap 512 \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "${NET_CONFIG}" \
  --unprivileged 1 \
  --onboot 1

pct start "${CTID}"

echo "==> Esperando rede do container"
for _ in $(seq 1 30); do
  pct exec "${CTID}" -- getent hosts deb.debian.org >/dev/null 2>&1 && break
  sleep 2
done

echo "==> Instalando dependências (python3, git, curl)"
pct exec "${CTID}" -- bash -c "
  set -e
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates
"

echo "==> Instalando o CLI agy (instalador oficial)"
pct exec "${CTID}" -- bash -c 'curl -fsSL https://antigravity.google/cli/install.sh | bash'

echo "==> Clonando ${REPO_URL}"
pct exec "${CTID}" -- bash -c "git clone --depth 1 '${REPO_URL}' /opt/agy-gateway-ng"

echo "==> Criando venv e instalando dependências Python"
pct exec "${CTID}" -- bash -c "
  set -e
  cd /opt/agy-gateway-ng
  python3 -m venv .venv
  .venv/bin/pip install --no-cache-dir -r requirements.txt
  cp .env.example .env
  sed -i 's#^AGY_BIN=.*#AGY_BIN=/root/.local/bin/agy#' .env
"

echo "==> Registrando serviço systemd"
pct exec "${CTID}" -- bash -c "cat > /etc/systemd/system/agy-gateway.service" <<UNIT
[Unit]
Description=agy-gateway (OpenAI-compatible local proxy pro agy CLI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/agy-gateway-ng
EnvironmentFile=-/opt/agy-gateway-ng/.env
Environment=HOME=/root
ExecStart=/opt/agy-gateway-ng/.venv/bin/uvicorn main:app --host 0.0.0.0 --port ${GATEWAY_PORT}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT

pct exec "${CTID}" -- bash -c "systemctl daemon-reload && systemctl enable --now agy-gateway"

CT_IP=$(pct exec "${CTID}" -- hostname -I | awk '{print $1}')

cat <<INFO

===================================================================
 agy-gateway-ng rodando no LXC ${CTID} (${HOSTNAME})
 http://${CT_IP}:${GATEWAY_PORT}/health

 Falta autenticar o agy (login Google via OAuth). Rode:
   pct exec ${CTID} -- agy
 (mostra uma URL — abra num navegador fora do container pra logar)

 Config em /opt/agy-gateway-ng/.env dentro do container (ex: AGY_API_KEY
 pra exigir auth) — depois de editar: pct exec ${CTID} -- systemctl restart agy-gateway
===================================================================
INFO
