#!/usr/bin/env bash
# In-container installer for capaciteit.
# Runs inside a Debian/Ubuntu LXC or VM. Safe to re-run: it upgrades in place
# and never overwrites /etc/capaciteit/capaciteit.env.
#
#   bash install.sh [--repo URL] [--ref BRANCH]

set -Eeuo pipefail

REPO="${REPO:-https://github.com/jeffreyvm/evcc-capaciteitstarief-bridge}"
REF="${REF:-main}"
APP_DIR=/opt/capaciteit
ENV_DIR=/etc/capaciteit
DATA_DIR=/var/lib/capaciteit
USER_NAME=capaciteit

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --ref)  REF="$2";  shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

msg() { echo -e "\e[1;34m==>\e[0m $*"; }
die() { echo -e "\e[1;31mfout:\e[0m $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"

msg "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl ca-certificates >/dev/null

msg "Creating service user and directories"
id -u "$USER_NAME" &>/dev/null || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$APP_DIR" "$ENV_DIR" "$DATA_DIR"

msg "Fetching capaciteit ($REF)"
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch --depth 1 origin "$REF"
  git -C "$APP_DIR" reset --hard "origin/$REF"
else
  git clone --depth 1 --branch "$REF" "$REPO" "$APP_DIR"
fi

msg "Building virtualenv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR"

msg "Installing configuration"
if [[ ! -f "$ENV_DIR/capaciteit.env" ]]; then
  install -m 0640 "$APP_DIR/deploy/capaciteit.env.example" "$ENV_DIR/capaciteit.env"
  NEW_CONFIG=1
else
  echo "    keeping existing $ENV_DIR/capaciteit.env"
fi
chown -R "$USER_NAME:$USER_NAME" "$APP_DIR" "$DATA_DIR"
chown root:"$USER_NAME" "$ENV_DIR/capaciteit.env"
chmod 0640 "$ENV_DIR/capaciteit.env"

msg "Installing service"
install -m 0644 "$APP_DIR/deploy/capaciteit.service" /etc/systemd/system/capaciteit.service
systemctl daemon-reload
systemctl enable --now capaciteit >/dev/null

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
PORT=$(grep -E '^WEB_PORT=' "$ENV_DIR/capaciteit.env" | cut -d= -f2 || echo 8099)

echo
msg "capaciteit is running"
echo "    dashboard   http://${IP:-<ip>}:${PORT:-8099}"
echo "    config      $ENV_DIR/capaciteit.env"
echo "    logs        journalctl -u capaciteit -f"
if [[ -n "${NEW_CONFIG:-}" ]]; then
  echo
  echo "    Next: set EVCC_URL and EVCC_API_KEY, then"
  echo "          systemctl restart capaciteit"
  echo "    It starts in DRY_RUN mode and will not touch evcc until you say so."
fi
