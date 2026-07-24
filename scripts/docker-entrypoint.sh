#!/bin/sh
set -eu

umask 077

runtime_root="/var/lib/recruitment-assistant"
mkdir -p \
  "${runtime_root}/config" \
  "${runtime_root}/data/attachments" \
  "${runtime_root}/data/backups" \
  "${runtime_root}/data/browser_state" \
  "${runtime_root}/data/exports" \
  "${runtime_root}/data/snapshots" \
  "${runtime_root}/data/themes" \
  "${runtime_root}/logs"
touch "${runtime_root}/config/app.env"
chmod 0600 "${runtime_root}/config/app.env"

for default_theme in /opt/recruitment-assistant-defaults/themes/*.css; do
  [ -f "${default_theme}" ] || continue
  theme_name=${default_theme##*/}
  if [ ! -e "${runtime_root}/data/themes/${theme_name}" ]; then
    cp "${default_theme}" "${runtime_root}/data/themes/${theme_name}"
  fi
done

exec "$@"
