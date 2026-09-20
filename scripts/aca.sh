#!/bin/sh
# Start or stop the hosted worker and lab Temporal server (the API wakes on its own).
#   scripts/aca.sh up|down|status <resource-group>
set -eu
action=${1:?usage: aca.sh up|down|status <resource-group>}
rg=${2:?usage: aca.sh up|down|status <resource-group>}

apps=$(az containerapp list -g "$rg" --query "[?contains(name, '-worker-') || contains(name, '-temporal-')].name" -o tsv)
[ -n "$apps" ] || { echo "no worker/temporal container apps found in $rg" >&2; exit 1; }

case "$action" in
  up)   replicas=1 ;;
  down) replicas=0 ;;
  status)
    az containerapp list -g "$rg" -o table \
      --query "[].{app:name, min:properties.template.scale.minReplicas, running:properties.runningStatus}"
    exit 0 ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac

# Temporal first on the way up, last on the way down.
[ "$action" = up ] && apps=$(echo "$apps" | sort -r) || apps=$(echo "$apps" | sort)
for app in $apps; do
  echo "$app -> min replicas $replicas"
  az containerapp update -g "$rg" -n "$app" --min-replicas "$replicas" --max-replicas 1 -o none
done
