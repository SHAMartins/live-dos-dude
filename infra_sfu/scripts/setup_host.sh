#!/usr/bin/env bash
# Prepara uma VM Ubuntu (22.04/24.04, x86 ou Ampere/ARM) da OCI.
#
#   sudo bash setup_host.sh            # VM do LiveKit (padrão)
#   sudo bash setup_host.sh backend    # VM do backend: só 80/443
#
# Idempotente: pode rodar de novo sem duplicar regras.
#
# Por que existe: as imagens Ubuntu da Oracle vêm com um iptables que REJEITA
# tudo, exceto SSH, mesmo depois de você liberar as portas na Security List.
# É preciso abrir as portas nos dois lugares.

set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Execute com sudo: sudo bash $0" >&2
  exit 1
fi

. /etc/os-release
if [[ "${ID}" != "ubuntu" ]]; then
  echo "Este script foi feito para Ubuntu (encontrado: ${ID})." >&2
  echo "No Oracle Linux use firewall-cmd para liberar as mesmas portas." >&2
  exit 1
fi

PERFIL=${1:-sfu}
# proto  porta(s)      motivo
case "${PERFIL}" in
  sfu)
    PORTS=(
      "tcp 80"           # ACME HTTP-01 (emissão/renovação do certificado)
      "tcp 443"          # sinalização wss:// via Caddy
      "tcp 7881"         # ICE sobre TCP (fallback quando UDP é bloqueado)
      "udp 50000:60000"  # mídia WebRTC
    )
    ;;
  backend)
    PORTS=(
      "tcp 80"           # ACME HTTP-01
      "tcp 443"          # API, WebSocket dos PCs, página do espectador, webhook
    )
    ;;
  *)
    echo "Perfil desconhecido: ${PERFIL} (use 'sfu' ou 'backend')" >&2
    exit 1
    ;;
esac

RULES_FILE=/etc/iptables/rules.v4

open_port() {
  local proto=$1 port=$2
  local spec=(-p "${proto}" -m conntrack --ctstate NEW -m "${proto}" --dport "${port}" -j ACCEPT)

  # 1) Regra ativa agora, inserida ANTES do REJECT genérico da OCI.
  if ! iptables -C INPUT "${spec[@]}" 2>/dev/null; then
    local pos
    pos=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')
    if [[ -n "${pos}" ]]; then
      iptables -I INPUT "${pos}" "${spec[@]}"
    else
      iptables -A INPUT "${spec[@]}"
    fi
    echo "   + ${proto}/${port}"
  else
    echo "   = ${proto}/${port} (já liberada)"
  fi

  # 2) Persistência: edita o rules.v4 direto em vez de usar
  #    'netfilter-persistent save', que gravaria também as cadeias do Docker
  #    e causaria conflito no próximo boot.
  if [[ -f "${RULES_FILE}" ]]; then
    local line="-A INPUT ${spec[*]}"
    if ! grep -qxF -- "${line}" "${RULES_FILE}"; then
      if grep -q '^-A INPUT -j REJECT' "${RULES_FILE}"; then
        sed -i "0,/^-A INPUT -j REJECT/s//${line//\//\\/}\n&/" "${RULES_FILE}"
      else
        sed -i "/^COMMIT/i ${line}" "${RULES_FILE}"
      fi
    fi
  fi
}

echo "==> Liberando portas no iptables do host (perfil ${PERFIL})"
for entry in "${PORTS[@]}"; do
  # shellcheck disable=SC2086
  open_port ${entry}
done
if [[ ! -f "${RULES_FILE}" ]]; then
  echo "   ! ${RULES_FILE} não existe: regras valem só até o próximo reboot." >&2
fi

echo "==> Ajustando buffers UDP (evita perda de pacotes em picos de bitrate)"
cat > /etc/sysctl.d/90-livekit.conf <<'EOF'
net.core.rmem_max = 5000000
net.core.wmem_max = 5000000
EOF
sysctl --quiet --system

MEM_MB=$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo)
if (( MEM_MB < 2048 )) && ! swapon --show | grep -q /swapfile; then
  echo "==> Só ${MEM_MB} MB de RAM: criando swap de 2 GB (evita falta de memória no build do Docker)"
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap -q /swapfile
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "==> Instalando Docker (pacotes do Ubuntu)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2
systemctl enable --now docker

TARGET_USER=${SUDO_USER:-ubuntu}
if id "${TARGET_USER}" &>/dev/null && ! id -nG "${TARGET_USER}" | grep -qw docker; then
  usermod -aG docker "${TARGET_USER}"
  echo "   ${TARGET_USER} adicionado ao grupo docker (saia e entre de novo no SSH)."
fi

echo
echo "Pronto. Confira as regras com:  sudo iptables -L INPUT -n --line-numbers"
echo "Próximo passo: cp .env.example .env, preencher e rodar 'docker compose up -d'."
