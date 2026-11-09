#!/usr/bin/env bash
# Creates/updates the "IP Function" custom field on IP addresses. No config edit, no restart.
#   sudo bash contrib/setup-ip-function.sh                 # NetBox in /opt/netbox
#   sudo bash contrib/setup-ip-function.sh /opt/netbox-4.6.9
set -uo pipefail
NB="${1:-/opt/netbox}"
PY="$NB/venv/bin/python"
SRC="$(cd "$(dirname "$0")" && pwd)/ip_function_field.py"
[ -x "$PY" ] || { echo "ERROR: $PY not found. Pass the NetBox path, e.g. /opt/netbox-4.6.9"; exit 1; }
cd "$NB/netbox" || exit 1
OUT="$("$PY" manage.py shell --no-imports < "$SRC" 2>&1)"; RC=$?
echo "$OUT" | grep -v "objects imported automatically"
if [ $RC -ne 0 ] || echo "$OUT" | grep -q "Traceback"; then
    echo "FAILED: nothing was changed by the failing step (see the error above)."
    exit 1
fi
