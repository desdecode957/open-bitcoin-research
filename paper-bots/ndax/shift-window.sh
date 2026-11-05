#!/usr/bin/env bash
# Manually move the NDAX PAPER buying reference; keep open-position sell target.
set -euo pipefail
bot_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
service_name=ndax-overton-paper
was_active=0
restore_service() {
  result=$?
  trap - EXIT
  if [ "$was_active" -eq 1 ]; then
    if ! sudo systemctl start "$service_name"; then
      echo "Paper bot restart failed; inspect its service status."
      result=1
    fi
  fi
  exit "$result"
}
trap restore_service EXIT
if systemctl is-active --quiet "$service_name"; then
  was_active=1
  sudo systemctl stop "$service_name"
fi
python3 "$bot_dir/ndax_paper.py" --shift-window "$@"
