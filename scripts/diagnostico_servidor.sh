#!/bin/bash
# Radiografía de una Mac para decidir si sirve como servidor casero.
#
# Se corre en la máquina candidata y se pega la salida completa:
#
#   bash diagnostico_servidor.sh
#
# No cambia nada, sólo lee. Lo que importa para un servidor no es la
# velocidad sino tres cosas: qué macOS admite (decide qué software se
# puede instalar), cuánta RAM tiene (decide si cabe Docker, que en Mac
# corre en una máquina virtual) y si el disco es SSD con espacio.

seccion() { printf '\n══ %s ══\n' "$1"; }

seccion "Modelo y año"
system_profiler SPHardwareDataType 2>/dev/null \
    | grep -E "Model Name|Model Identifier|Chip|Processor Name|Processor Speed|Total Number of Cores|Memory" \
    | sed 's/^ *//'

seccion "macOS"
echo "instalado: $(sw_vers -productName) $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
echo "arquitectura: $(uname -m)"

seccion "CPU"
sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "(Apple Silicon, ver Chip arriba)"
echo "núcleos: $(sysctl -n hw.ncpu) · carga ahora: $(uptime | sed 's/.*load averages*: //')"

seccion "RAM"
total=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
echo "total: ${total} GB"
vm_stat | awk -v pg=$(sysctl -n hw.pagesize) '
    /Pages free/        {free=$3}
    /Pages inactive/    {inact=$3}
    /Pages speculative/ {spec=$3}
    END { gsub(/\./,"",free); gsub(/\./,"",inact); gsub(/\./,"",spec)
          printf "libre ahora: %.1f GB\n", (free+inact+spec)*pg/1024/1024/1024 }'

seccion "Disco"
df -h / | awk 'NR==2 {print "total: "$2" · usado: "$3" · libre: "$4" ("$5" usado)"}'
diskutil info / 2>/dev/null | grep -E "Solid State|Device / Media Name|File System Personality" | sed 's/^ *//'

seccion "Batería (si es laptop)"
if pmset -g batt 2>/dev/null | grep -q InternalBattery; then
    pmset -g batt | tail -1 | sed 's/^ *//'
    system_profiler SPPowerDataType 2>/dev/null | grep -E "Cycle Count|Condition|Maximum Capacity" | sed 's/^ *//'
else
    echo "sin batería: es de escritorio"
fi

seccion "Red"
for puerto in $(networksetup -listallhardwareports 2>/dev/null | awk '/Hardware Port/{$1=$2=""; print}' | sed 's/^ *//'); do :; done
networksetup -listallhardwareports 2>/dev/null | grep -E "Hardware Port|Device" | paste - - | sed 's/Hardware Port: //; s/Device: / → /'
echo "ip actual: $(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo '?')"

seccion "Acceso remoto"
if systemsetup -getremotelogin 2>/dev/null | grep -qi on; then
    echo "SSH (Sesión remota): ACTIVADO — se puede administrar desde otra máquina"
else
    echo "SSH (Sesión remota): apagado — Ajustes → General → Compartir → Sesión remota"
fi

seccion "Software base"
for h in brew docker colima python3 git tailscale; do
    if command -v "$h" >/dev/null 2>&1; then
        echo "$h: $(command -v "$h")"
    else
        echo "$h: no instalado"
    fi
done
ls /Applications 2>/dev/null | grep -iE "docker|orbstack|tailscale" | sed 's/^/app: /'

seccion "Tiempo encendida y energía"
echo "encendida desde: $(uptime | sed 's/.*up //; s/,.*//')"
pmset -g custom 2>/dev/null | grep -E "^ *(sleep|displaysleep|womp|autorestart)" | sed 's/^ *//' | paste - - - -
