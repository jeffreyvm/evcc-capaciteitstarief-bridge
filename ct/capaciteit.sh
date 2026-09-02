#!/usr/bin/env bash
# capaciteit — Proxmox VE helper script
#
# Creates an unprivileged Debian LXC, installs capaciteit, and starts it in
# dry-run mode. Run on the Proxmox host:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/jeffreyvm/evcc-capaciteitstarief-bridge/main/ct/capaciteit.sh)"
#
# Standalone by design: it does not source build.func from another project, so
# nothing outside this repo can change what runs on your hypervisor. Read it
# before you pipe it into a shell — that goes for every script that asks this.

set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/jeffreyvm/evcc-capaciteitstarief-bridge}"
RAW_URL="${RAW_URL:-https://raw.githubusercontent.com/jeffreyvm/evcc-capaciteitstarief-bridge/main}"
REF="${REF:-main}"

APP="capaciteit"
DEFAULT_HOSTNAME="capaciteit"
DEFAULT_CORES=1
DEFAULT_RAM=512
DEFAULT_DISK=4
DEFAULT_BRIDGE="vmbr0"
TEMPLATE_PATTERN="debian-12-standard"

# --- output ------------------------------------------------------------------
BL='\033[1;34m'; GN='\033[1;32m'; RD='\033[1;31m'; YW='\033[1;33m'; CL='\033[0m'
msg()  { echo -e "${BL}==>${CL} $*"; }
ok()   { echo -e "${GN} ok ${CL} $*"; }
warn() { echo -e "${YW}let op${CL} $*"; }
die()  { echo -e "${RD}fout${CL} $*" >&2; exit 1; }

cleanup_on_error() {
  local code=$?
  [[ $code -eq 0 ]] && return
  echo
  die "installatie afgebroken (exit $code). Container ${CTID:-?} is niet verwijderd — inspecteer met: pct config ${CTID:-?}"
}
trap cleanup_on_error EXIT

# --- preflight ---------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "draai dit als root op de Proxmox host"
command -v pveversion >/dev/null || die "dit is geen Proxmox VE host"
command -v whiptail   >/dev/null || die "whiptail ontbreekt (apt install whiptail)"

clear
cat <<'BANNER'
   capaciteit
   Belgisch capaciteitstarief — piekscheren voor evcc
BANNER
echo

# --- settings ----------------------------------------------------------------
CTID=""; HOSTNAME="$DEFAULT_HOSTNAME"; CORES=$DEFAULT_CORES
RAM=$DEFAULT_RAM; DISK=$DEFAULT_DISK; BRIDGE="$DEFAULT_BRIDGE"
NET="dhcp"; STORAGE=""; PASSWORD=""

pick_storage() {
  local -a options=()
  while read -r name _; do
    [[ -n "$name" ]] && options+=("$name" "")
  done < <(pvesm status -content rootdir 2>/dev/null | awk 'NR>1 {print $1}')
  [[ ${#options[@]} -gt 0 ]] || die "geen storage gevonden die container-volumes ondersteunt"
  if [[ ${#options[@]} -eq 2 ]]; then
    STORAGE="${options[0]}"
  else
    STORAGE=$(whiptail --title "Storage" --menu \
      "Waar komt de container-disk?" 16 60 6 "${options[@]}" 3>&1 1>&2 2>&3) \
      || die "afgebroken"
  fi
}

advanced_settings() {
  HOSTNAME=$(whiptail --inputbox "Hostname" 8 60 "$DEFAULT_HOSTNAME" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  CORES=$(whiptail --inputbox "CPU cores" 8 60 "$DEFAULT_CORES" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  RAM=$(whiptail --inputbox "RAM (MB)" 8 60 "$DEFAULT_RAM" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  DISK=$(whiptail --inputbox "Disk (GB)" 8 60 "$DEFAULT_DISK" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  BRIDGE=$(whiptail --inputbox "Netwerkbrug" 8 60 "$DEFAULT_BRIDGE" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  NET=$(whiptail --inputbox "IP (dhcp, of 192.168.10.50/24)" 8 60 "dhcp" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  if [[ "$NET" != "dhcp" ]]; then
    GATEWAY=$(whiptail --inputbox "Gateway" 8 60 "192.168.10.1" --title "Instellingen" 3>&1 1>&2 2>&3) || die "afgebroken"
  fi
  PASSWORD=$(whiptail --passwordbox "Root-wachtwoord (leeg = geen console-login)" 8 60 --title "Instellingen" 3>&1 1>&2 2>&3) || true
  pick_storage
}

if whiptail --title "capaciteit" --yesno \
  "Standaardinstellingen gebruiken?\n\n  Debian 12, unprivileged\n  ${DEFAULT_CORES} core, ${DEFAULT_RAM} MB RAM, ${DEFAULT_DISK} GB disk\n  DHCP op ${DEFAULT_BRIDGE}\n\nKies Nee om alles zelf in te stellen." \
  15 62 --yes-button "Standaard" --no-button "Aanpassen"; then
  pick_storage
else
  advanced_settings
fi

CTID="${CTID:-$(pvesh get /cluster/nextid)}"

# --- template ----------------------------------------------------------------
msg "Template zoeken"
TEMPLATE_STORE=$(pvesm status -content vztmpl 2>/dev/null | awk 'NR==2 {print $1}')
[[ -n "$TEMPLATE_STORE" ]] || die "geen storage met content-type vztmpl"

TEMPLATE=$(pveam list "$TEMPLATE_STORE" 2>/dev/null | awk -v p="$TEMPLATE_PATTERN" '$1 ~ p {print $1}' | sort -V | tail -1)
if [[ -z "$TEMPLATE" ]]; then
  msg "Debian 12 template downloaden"
  pveam update >/dev/null
  AVAILABLE=$(pveam available -section system | awk -v p="$TEMPLATE_PATTERN" '$2 ~ p {print $2}' | sort -V | tail -1)
  [[ -n "$AVAILABLE" ]] || die "geen $TEMPLATE_PATTERN template beschikbaar"
  pveam download "$TEMPLATE_STORE" "$AVAILABLE" >/dev/null
  TEMPLATE="$TEMPLATE_STORE:vztmpl/$AVAILABLE"
fi
ok "template $TEMPLATE"

# --- create ------------------------------------------------------------------
msg "Container $CTID aanmaken"
NET_CONF="name=eth0,bridge=${BRIDGE},ip=${NET}"
[[ "$NET" != "dhcp" && -n "${GATEWAY:-}" ]] && NET_CONF="${NET_CONF},gw=${GATEWAY}"

CREATE_ARGS=(
  "$CTID" "$TEMPLATE"
  --hostname "$HOSTNAME"
  --cores "$CORES"
  --memory "$RAM"
  --swap 256
  --rootfs "${STORAGE}:${DISK}"
  --net0 "$NET_CONF"
  --unprivileged 1
  --features nesting=1
  --onboot 1
  --ostype debian
  --description "capaciteit — capaciteitstarief controller for evcc
${REPO_URL}"
)
[[ -n "$PASSWORD" ]] && CREATE_ARGS+=(--password "$PASSWORD")

pct create "${CREATE_ARGS[@]}" >/dev/null
ok "container aangemaakt"

msg "Starten"
pct start "$CTID"

msg "Wachten op netwerk"
for i in $(seq 1 30); do
  if pct exec "$CTID" -- getent hosts deb.debian.org >/dev/null 2>&1; then
    ok "netwerk actief"; break
  fi
  [[ $i -eq 30 ]] && die "container kreeg geen netwerk — controleer brug $BRIDGE"
  sleep 2
done

# --- install -----------------------------------------------------------------
msg "capaciteit installeren"
pct exec "$CTID" -- bash -c "
  set -Eeuo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq curl ca-certificates git >/dev/null
  curl -fsSL '${RAW_URL}/deploy/install.sh' -o /tmp/install.sh
  bash /tmp/install.sh --repo '${REPO_URL}' --ref '${REF}'
"

IP=$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')
trap - EXIT

cat <<EOF

$(echo -e "${GN}capaciteit draait${CL}")

  Dashboard     http://${IP}:8099
  Container     ${CTID} (${HOSTNAME})
  Configuratie  pct exec ${CTID} -- nano /etc/capaciteit/capaciteit.env
  Logs          pct exec ${CTID} -- journalctl -u capaciteit -f

$(echo -e "${YW}Volgende stap${CL}")
  Zet EVCC_URL en EVCC_API_KEY in de configuratie en herstart:
      pct exec ${CTID} -- systemctl restart capaciteit

  De service start in proefdraaimodus (DRY_RUN=true): hij rekent alles door
  en toont het op het dashboard, maar stuurt evcc niet aan. Laat dat een paar
  dagen staan. Zet DRY_RUN=false pas als de beslissingen kloppen.

EOF
