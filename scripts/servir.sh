#!/bin/bash
# Deja la aplicación corriendo para acceder desde otros dispositivos.
#
#   ./scripts/servir.sh          arranca (o reinicia) en segundo plano
#   ./scripts/servir.sh parar    la detiene
#
# `caffeinate -is` evita que la Mac duerma mientras el proceso viva. No
# evita el sueño por cerrar la tapa: una laptop con la tapa cerrada se
# duerme salvo que tenga monitor externo. Déjala abierta y enchufada.
#
# Escucha en todas las interfaces para que el celular la vea; con
# Tailscale, nada de esto sale a internet abierto.

set -euo pipefail
cd "$(dirname "$0")/.."

REGISTRO="logs/servidor.log"
PID_FILE="logs/servidor.pid"
mkdir -p logs

parar() {
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        kill "$(cat "$PID_FILE")" && echo "detenido (pid $(cat "$PID_FILE"))"
    else
        pkill -f "streamlit run app.py" && echo "detenido" || echo "no estaba corriendo"
    fi
    rm -f "$PID_FILE"
}

if [[ "${1:-}" == "parar" ]]; then
    parar
    exit 0
fi

parar >/dev/null 2>&1 || true

# XSRF y CORS van apagados sólo aquí: con ellos, un navegador que llega
# por una IP distinta de localhost carga el HTML pero el WebSocket se
# rechaza y la app se queda «cargando» para siempre. Detrás de Tailscale
# la red ya es privada, que es lo que esas dos protecciones suplen.
nohup caffeinate -is .venv/bin/python -m streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port 8501 \
    --server.headless true \
    --server.fileWatcherType none \
    --server.enableXsrfProtection false \
    --server.enableCORS false \
    >>"$REGISTRO" 2>&1 &
echo $! >"$PID_FILE"

sleep 3
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "?")
    echo "corriendo (pid $(cat "$PID_FILE"))"
    echo "  en casa:       http://$IP:8501"
    echo "  con Tailscale: http://$(hostname -s).local:8501  (o la IP 100.x que te dé Tailscale)"
    echo "  registro:      $REGISTRO"
else
    echo "no arrancó; revisa $REGISTRO"
    tail -20 "$REGISTRO"
    exit 1
fi
