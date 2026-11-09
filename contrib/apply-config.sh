#!/usr/bin/env bash
# Enables the plugin in NetBox's configuration.py through a managed block.
#   sudo bash contrib/apply-config.sh            # NetBox in /opt/netbox
#   sudo bash contrib/apply-config.sh /opt/netbox-4.6.9
# Safe to re-run: the previous managed block is replaced, not duplicated.
# Takes a timestamped backup and restores it automatically if NetBox's config check fails.
# Does NOT restart NetBox; it prints the restart command when the check passes.
set -euo pipefail

NB="${1:-/opt/netbox}"
CFG="$NB/netbox/netbox/configuration.py"
PY="$NB/venv/bin/python"
BLOCK="$(cd "$(dirname "$0")" && pwd)/netbox_config_block.py"
BEGIN='# >>> netbox-subnet-wizard managed block'
END='# <<< netbox-subnet-wizard managed block <<<'

[ -f "$CFG" ]   || { echo "ERROR: $CFG not found. Pass the NetBox path, e.g. /opt/netbox-4.6.9"; exit 1; }
[ -x "$PY" ]    || { echo "ERROR: $PY not found"; exit 1; }
[ -f "$BLOCK" ] || { echo "ERROR: $BLOCK not found"; exit 1; }

BAK="$CFG.bak.$(date +%Y%m%d-%H%M%S)"
cp -p "$CFG" "$BAK"
echo "Backup: $BAK"

# Drop any previous managed block, then append the current one
sed -i "\|^$BEGIN|,\|^$END|d" "$CFG"
printf '\n' >> "$CFG"
cat "$BLOCK" >> "$CFG"

if (cd "$NB/netbox" && "$PY" manage.py check >/tmp/netbox-config-check.log 2>&1); then
    echo "OK: configuration check passed."
    (cd "$NB/netbox" && "$PY" - <<'PYEOF'
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'netbox.settings')
django.setup()
from django.conf import settings
print('Plugins :', ', '.join(settings.PLUGINS))
PYEOF
    ) 2>/dev/null || true
    echo "Next: sudo systemctl restart netbox netbox-rq"
else
    cp -p "$BAK" "$CFG"
    echo "FAILED: configuration check failed; original configuration restored from $BAK"
    echo "Details: /tmp/netbox-config-check.log"
    tail -5 /tmp/netbox-config-check.log
    exit 1
fi
