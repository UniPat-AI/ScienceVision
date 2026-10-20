#!/usr/bin/bash
# Root-owned activation path for the staged password-management release.
set -Eeuo pipefail
umask 077

PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly INSTALLED_SCRIPT=/usr/local/sbin/babyvision-task-manager-pw-activate-root
readonly SERVICE=babyvision-task-manager.service
readonly CANDIDATE_DIR=/home/matianyi/services/babyvision-task-manager/c/pw-dff3b35adefd-root
readonly RELEASE_DIR=/home/matianyi/services/babyvision-task-manager/releases/d6d4e103-pw-dff3b35adefd
readonly CANDIDATE_UNIT="$CANDIDATE_DIR/$SERVICE"
readonly LIVE_UNIT=/etc/systemd/system/babyvision-task-manager.service
readonly APP_MANIFEST="$RELEASE_DIR/APP-MANIFEST.sha256"
readonly EXPECTED_WORKING_DIRECTORY="$RELEASE_DIR/app"
readonly EXPECTED_LIVE_UNIT_SHA256=15584cce2055f812a581fba4c96a05fd03dd33979ef6408d6274d647af3df543
readonly EXPECTED_CANDIDATE_UNIT_SHA256=68bc0e2f335aa1743e31a7dcbbaa3e8f061dfe07de1b3f084ae8efe9ac40ea85
readonly EXPECTED_APP_MANIFEST_SHA256=dff3b35adefd0a350cfff90bbc2d58e9e4396356efabfcd7d61d2216adcc668a
readonly ROOT_TMP_PARENT=/run/babyvision-task-manager-activation
readonly BACKUP_PARENT=/var/lib/babyvision-task-manager/activation-backups
readonly PROBE_URL=http://127.0.0.1:4174/sv-annotate
readonly EXPECTED_PROBE_CODE=303
readonly EXPECTED_PROBE_LOCATION=/sv-annotate/login/

root_tmp=''
backup_dir=''
changed=0
rolling_back=0

cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "$root_tmp" && "$root_tmp" == "$ROOT_TMP_PARENT"/activation.* && -d "$root_tmp" ]]; then
    rm -rf -- "$root_tmp" || true
  fi
  exit "$status"
}

rollback() {
  local rollback_ok=1
  local backup_unit
  local restored_hash

  [[ $changed -eq 1 && $rolling_back -eq 0 ]] || return 0
  rolling_back=1
  trap - ERR
  set +e

  backup_unit="$backup_dir/$SERVICE"
  printf '%s\n' "Activation failed; attempting to restore $SERVICE from $backup_unit" >&2

  if [[ -z "$backup_dir" || ! -f "$backup_unit" ]]; then
    printf '%s\n' "Rollback backup is unavailable: $backup_unit" >&2
    return 0
  fi

  restored_hash=$(sha256sum -- "$backup_unit" | awk '{print $1}')
  if [[ "$restored_hash" != "$EXPECTED_LIVE_UNIT_SHA256" ]]; then
    printf '%s\n' 'Rollback backup hash is not the expected live unit hash' >&2
    return 0
  fi

  install -o root -g root -m 0644 -- "$backup_unit" "$LIVE_UNIT" || rollback_ok=0
  systemctl daemon-reload || rollback_ok=0
  systemctl restart "$SERVICE" || rollback_ok=0
  systemctl is-active --quiet "$SERVICE" || rollback_ok=0

  restored_hash=$(sha256sum -- "$LIVE_UNIT" | awk '{print $1}')
  [[ "$restored_hash" == "$EXPECTED_LIVE_UNIT_SHA256" ]] || rollback_ok=0

  if [[ $rollback_ok -eq 1 ]]; then
    printf '%s\n' "Rollback restored and restarted $SERVICE" >&2
  else
    printf '%s\n' "Rollback did not fully restore $SERVICE; inspect $backup_dir" >&2
  fi
}

fatal() {
  printf 'ERROR: %s\n' "$*" >&2
  rollback
  exit 1
}

on_error() {
  local status=$?
  trap - ERR
  printf 'ERROR: unexpected command failure (exit %d)\n' "$status" >&2
  rollback
  exit "$status"
}

expect_sha256() {
  local path=$1
  local expected=$2
  local label=$3
  local actual

  actual=$(sha256sum -- "$path" | awk '{print $1}') || fatal "Cannot hash $label"
  [[ "$actual" == "$expected" ]] || fatal "$label hash mismatch"
}

trap on_error ERR
trap cleanup EXIT

if [[ $EUID -ne 0 ]]; then
  printf '%s\n' "Refusing non-root execution. Bootstrap $INSTALLED_SCRIPT, then execute that root-owned copy." >&2
  exit 1
fi

if [[ $0 != "$INSTALLED_SCRIPT" ]]; then
  fatal "Refusing to run outside $INSTALLED_SCRIPT"
fi

for required_command in awk curl grep install mktemp rm sha256sum stat systemctl; do
  command -v "$required_command" >/dev/null 2>&1 || fatal "Required command is unavailable: $required_command"
done

script_owner=$(stat -c '%u:%g' -- "$INSTALLED_SCRIPT") || fatal 'Cannot inspect installed activation script'
script_mode=$(stat -c '%a' -- "$INSTALLED_SCRIPT") || fatal 'Cannot inspect installed activation script mode'
[[ "$script_owner" == 0:0 ]] || fatal 'Installed activation script is not owned by root:root'
(( (8#$script_mode & 0022) == 0 )) || fatal 'Installed activation script is writable by group or other'

[[ -d "$RELEASE_DIR/app" ]] || fatal "Missing staged release app tree: $RELEASE_DIR/app"
[[ -f "$CANDIDATE_UNIT" ]] || fatal "Missing candidate unit: $CANDIDATE_UNIT"
[[ -f "$APP_MANIFEST" ]] || fatal "Missing release manifest: $APP_MANIFEST"
[[ -f "$LIVE_UNIT" ]] || fatal "Missing live unit: $LIVE_UNIT"

install -d -o root -g root -m 0700 -- "$ROOT_TMP_PARENT" || fatal 'Cannot create root staging directory'
root_tmp=$(mktemp -d "$ROOT_TMP_PARENT/activation.XXXXXXXX") || fatal 'Cannot create root staging directory'
[[ $(stat -c '%u:%g:%a' -- "$root_tmp") == 0:0:700 ]] || fatal 'Root staging directory has unsafe ownership or mode'

candidate_unit_copy="$root_tmp/candidate-unit.service"
manifest_copy="$root_tmp/APP-MANIFEST.sha256"
live_unit_copy="$root_tmp/live-unit.service"

# Copy untrusted candidate files first; all subsequent verification uses root-owned copies.
install -o root -g root -m 0600 -- "$CANDIDATE_UNIT" "$candidate_unit_copy" || fatal 'Cannot copy candidate unit into root staging'
install -o root -g root -m 0600 -- "$APP_MANIFEST" "$manifest_copy" || fatal 'Cannot copy app manifest into root staging'
[[ $(stat -c '%u:%g:%a' -- "$candidate_unit_copy") == 0:0:600 ]] || fatal 'Candidate unit copy is not root-owned'
[[ $(stat -c '%u:%g:%a' -- "$manifest_copy") == 0:0:600 ]] || fatal 'App manifest copy is not root-owned'

expect_sha256 "$candidate_unit_copy" "$EXPECTED_CANDIDATE_UNIT_SHA256" 'Candidate unit'
expect_sha256 "$manifest_copy" "$EXPECTED_APP_MANIFEST_SHA256" 'App manifest'

working_directory_count=$(grep -c '^WorkingDirectory=' "$candidate_unit_copy" || true)
[[ "$working_directory_count" == 1 ]] || fatal 'Candidate unit has an invalid WorkingDirectory count'
grep -Fqx -- "WorkingDirectory=$EXPECTED_WORKING_DIRECTORY" "$candidate_unit_copy" || fatal 'Candidate unit WorkingDirectory is not the staged release'

# This reads every application file as root against the integrity-bound manifest copy.
if ! (cd "$RELEASE_DIR" && sha256sum -c --strict --status "$manifest_copy"); then
  fatal 'Staged application does not match its expected manifest'
fi

install -o root -g root -m 0600 -- "$LIVE_UNIT" "$live_unit_copy" || fatal 'Cannot copy live unit into root staging'
[[ $(stat -c '%u:%g:%a' -- "$live_unit_copy") == 0:0:600 ]] || fatal 'Live unit copy is not root-owned'
expect_sha256 "$live_unit_copy" "$EXPECTED_LIVE_UNIT_SHA256" 'Live unit'

install -d -o root -g root -m 0700 -- "$BACKUP_PARENT" || fatal 'Cannot create root backup directory'
backup_dir=$(mktemp -d "$BACKUP_PARENT/activation.XXXXXXXX") || fatal 'Cannot create root backup directory'
backup_unit="$backup_dir/$SERVICE"
install -o root -g root -m 0600 -- "$live_unit_copy" "$backup_unit" || fatal 'Cannot back up live unit'
expect_sha256 "$backup_unit" "$EXPECTED_LIVE_UNIT_SHA256" 'Live unit backup'
sha256sum -- "$backup_unit" > "$backup_dir/$SERVICE.sha256" || fatal 'Cannot record live unit backup hash'

# Set this before replacing the live unit so any installation failure is rolled back too.
expect_sha256 "$LIVE_UNIT" "$EXPECTED_LIVE_UNIT_SHA256" 'Live unit before installation'
changed=1
install -o root -g root -m 0644 -- "$candidate_unit_copy" "$LIVE_UNIT" || fatal 'Cannot install candidate unit'
expect_sha256 "$LIVE_UNIT" "$EXPECTED_CANDIDATE_UNIT_SHA256" 'Installed candidate unit'
systemctl daemon-reload || fatal 'systemd daemon-reload failed'
systemctl restart "$SERVICE" || fatal "Cannot restart $SERVICE"

ready=0
for ((attempt = 1; attempt <= 30; attempt++)); do
  probe_headers="$root_tmp/probe.headers"
  probe_code=$(curl -sS -o /dev/null -D "$probe_headers" --connect-timeout 2 --max-time 2 -w '%{http_code}' "$PROBE_URL" || true)
  probe_location=$(awk 'tolower($1) == "location:" { sub(/\\r$/, "", $2); print $2; exit }' "$probe_headers" 2>/dev/null || true)
  if [[ "$probe_code" == "$EXPECTED_PROBE_CODE" && "$probe_location" == "$EXPECTED_PROBE_LOCATION" ]]; then
    ready=1
    break
  fi
  sleep 1
done
[[ $ready -eq 1 ]] || fatal "Loopback readiness probe did not return $EXPECTED_PROBE_CODE to $EXPECTED_PROBE_LOCATION"

[[ $(systemctl show --property=ActiveState --value "$SERVICE") == active ]] || fatal "$SERVICE is not active"
[[ $(systemctl show --property=WorkingDirectory --value "$SERVICE") == "$EXPECTED_WORKING_DIRECTORY" ]] || fatal "$SERVICE did not adopt the staged WorkingDirectory"

printf '%s\n' "Activated $SERVICE with $EXPECTED_WORKING_DIRECTORY"
