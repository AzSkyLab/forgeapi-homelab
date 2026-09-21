#!/bin/sh
# Start or stop the hosted worker and lab Temporal server (the API wakes on its own).
#   scripts/aca.sh up|down|status <resource-group>
#
# `down` deactivates the apps' revisions. Lowering min replicas is NOT enough: these apps have no
# ingress-driven scale rule, so nothing ever tells Container Apps to scale them in, and they keep
# running (and billing) at min 0. `status` therefore reports replicas actually running.
# Re-run `down` after any update of the host deployment: a new revision starts active.
set -eu
action=${1:?usage: aca.sh up|down|status <resource-group>}
rg=${2:?usage: aca.sh up|down|status <resource-group>}

names() { az containerapp list -g "$rg" --query "[?contains(name, '$1')].name" -o tsv; }
running() { az containerapp replica list -g "$rg" -n "$1" --query 'length(@)' -o tsv 2>/dev/null || echo 0; }
temporal=$(names -temporal-)
workers=$(names -worker-)
[ -n "$temporal$workers" ] || { echo "no worker/temporal container apps found in $rg" >&2; exit 1; }

case "$action" in
  status)
    for app in $(az containerapp list -g "$rg" --query '[].name' -o tsv); do
      echo "$app: $(running "$app") replica(s) running"
    done ;;
  up)
    for app in $temporal $workers; do   # Temporal first: the worker needs it
      latest=$(az containerapp show -g "$rg" -n "$app" --query properties.latestRevisionName -o tsv)
      echo "$app -> activating $latest, min replicas 1"
      az containerapp revision activate -g "$rg" -n "$app" --revision "$latest" -o none
      az containerapp update -g "$rg" -n "$app" --min-replicas 1 -o none
    done ;;
  down)
    for app in $workers $temporal; do   # worker first, so nothing is left polling a dead Temporal
      az containerapp update -g "$rg" -n "$app" --min-replicas 0 -o none
      for rev in $(az containerapp revision list -g "$rg" -n "$app" --query "[?properties.active].name" -o tsv); do
        echo "$app -> deactivating $rev"
        az containerapp revision deactivate -g "$rg" -n "$app" --revision "$rev" -o none
      done
    done ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac
