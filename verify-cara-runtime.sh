#!/usr/bin/env bash
# Live release gate for Cara + the isolated task worker.
set -euo pipefail

fail() {
  echo "verify-cara-runtime: $*" >&2
  exit 1
}

[ "$(id -u)" -eq 0 ] || fail "run as root"
systemctl is-active --quiet tg-ingest-agent || fail "Cara service is not active"
systemctl is-active --quiet cara-worker || fail "worker service is not active"
systemctl is-active --quiet cara-mentor || fail "Mentor service is not active"
systemctl is-active --quiet cara-mentor-runner ||
  fail "Mentor runner service is not active"

[ "$(systemctl show cara-worker -p User --value)" = "cara-worker" ] ||
  fail "worker user mismatch"
[ "$(systemctl show cara-worker -p Group --value)" = "cara-worker-spool" ] ||
  fail "worker group mismatch"
[ "$(systemctl show cara-worker -p PrivateNetwork --value)" = "yes" ] ||
  fail "worker network namespace is not private"
[ "$(systemctl show cara-worker -p NoNewPrivileges --value)" = "yes" ] ||
  fail "worker can gain privileges"
[ "$(systemctl show cara-worker -p ProtectSystem --value)" = "strict" ] ||
  fail "worker filesystem protection is not strict"
[ "$(systemctl show cara-worker -p ProtectHome --value)" = "yes" ] ||
  fail "worker home protection is not enabled"
[ "$(systemctl show cara-worker -p PrivateDevices --value)" = "yes" ] ||
  fail "worker devices are not private"
[ "$(systemctl show cara-worker -p RestrictNamespaces --value)" = "yes" ] ||
  fail "worker can create namespaces"
[ -z "$(systemctl show cara-worker -p CapabilityBoundingSet --value)" ] ||
  fail "worker capability bounding set is not empty"
case "$(systemctl show cara-worker -p ReadWritePaths --value)" in
  *"/var/lib/cara-worker"*) ;;
  *) fail "worker writable-path allowlist mismatch" ;;
esac
inaccessible="$(systemctl show cara-worker -p InaccessiblePaths --value)"
for protected in \
  /etc/tg-ingest-agent.env \
  /var/lib/tg-ingest-agent \
  /etc/tg-ingest-agent-backup.key \
  /etc/nikki-agent.env \
  /var/lib/nikki-agent \
  /etc/nikki-agent-backup.key \
  /etc/codex-auto-update/telegram.env
do
  case "$inaccessible" in
    *"$protected"*) ;;
    *) fail "worker inaccessible-path boundary omits $protected" ;;
  esac
done
[ "$(systemctl show cara-worker -p RestrictAddressFamilies --value)" = "AF_UNIX" ] ||
  fail "worker address-family boundary mismatch"
[ -n "$(systemctl show cara-worker -p SystemCallFilter --value)" ] ||
  fail "worker syscall filter is empty"
[ "$(systemctl show cara-worker -p MemoryMax --value)" = "268435456" ] ||
  fail "worker memory cap mismatch"
[ "$(systemctl show cara-worker -p TasksMax --value)" = "32" ] ||
  fail "worker task cap mismatch"

agent_groups="$(systemctl show tg-ingest-agent -p SupplementaryGroups --value)"
for narrow_group in cara-worker-spool cara-mentor-spool cara-mentor-runner-spool; do
  case " $agent_groups " in
    *" $narrow_group "*) ;;
    *) fail "Cara service lacks narrow group $narrow_group" ;;
  esac
done

for entry in \
  "/var/lib/cara-worker root cara-worker-spool 750" \
  "/var/lib/cara-worker/spool root cara-worker-spool 750" \
  "/var/lib/cara-worker/spool/requests tg-ingest cara-worker-spool 2750" \
  "/var/lib/cara-worker/spool/cancel tg-ingest cara-worker-spool 2750" \
  "/var/lib/cara-worker/spool/results cara-worker cara-worker-spool 2750"
do
  set -- $entry
  actual="$(stat -c '%U %G %a' "$1")"
  [ "$actual" = "$2 $3 $4" ] ||
    fail "bad ownership/mode on $1: $actual"
done

# Prove the one-way spool with the real service users, not a same-uid tempfile:
# Cara owns requests/cancel; the worker owns results; neither can erase the
# other side's evidence.
request_probe=/var/lib/cara-worker/spool/requests/.verify-owner
cancel_probe=/var/lib/cara-worker/spool/cancel/.verify-owner
result_probe=/var/lib/cara-worker/spool/results/.verify-owner
runuser -u tg-ingest -g tg-ingest -G cara-worker-spool -- \
  touch "$request_probe" "$cancel_probe"
runuser -u cara-worker -g cara-worker-spool -- touch "$result_probe"
if runuser -u cara-worker -g cara-worker-spool -- rm -f "$request_probe"; then
  fail "worker can delete Cara-owned requests"
fi
if runuser -u cara-worker -g cara-worker-spool -- rm -f "$cancel_probe"; then
  fail "worker can delete Cara-owned cancellation markers"
fi
if runuser -u tg-ingest -g tg-ingest -G cara-worker-spool -- \
    rm -f "$result_probe"; then
  fail "Cara can delete worker-owned results"
fi
runuser -u tg-ingest -g tg-ingest -G cara-worker-spool -- \
  rm -f "$request_probe" "$cancel_probe"
runuser -u cara-worker -g cara-worker-spool -- rm -f "$result_probe"

for unit_user in \
  "cara-mentor cara-mentor" \
  "cara-mentor-runner cara-mentor-runner"
do
  set -- $unit_user
  [ "$(systemctl show "$1" -p User --value)" = "$2" ] ||
    fail "$1 user mismatch"
  [ -z "$(systemctl show "$1" -p CapabilityBoundingSet --value)" ] ||
    fail "$1 capability bounding set is not empty"
  [ "$(systemctl show "$1" -p NoNewPrivileges --value)" = "yes" ] ||
    fail "$1 can gain privileges"
  [ "$(systemctl show "$1" -p ProtectSystem --value)" = "strict" ] ||
    fail "$1 filesystem protection is not strict"
done
[ "$(systemctl show cara-mentor -p PrivateNetwork --value)" = "no" ] ||
  fail "Mentor unexpectedly has no inference network"
[ "$(systemctl show cara-mentor-runner -p PrivateNetwork --value)" = "yes" ] ||
  fail "Mentor candidate runner network is not private"
[ "$(systemctl show cara-mentor-runner -p RestrictAddressFamilies --value)" = "AF_UNIX" ] ||
  fail "Mentor runner address-family boundary mismatch"

for entry in \
  "/var/lib/cara-mentor root cara-mentor-spool 750" \
  "/var/lib/cara-mentor/spool root cara-mentor-spool 750" \
  "/var/lib/cara-mentor/spool/requests tg-ingest cara-mentor-spool 2750" \
  "/var/lib/cara-mentor/spool/results cara-mentor cara-mentor-spool 2750" \
  "/var/lib/cara-mentor/inflight cara-mentor cara-mentor-spool 700" \
  "/var/lib/cara-mentor/usage cara-mentor cara-mentor-spool 700" \
  "/var/lib/cara-mentor-runner root cara-mentor-runner-spool 750" \
  "/var/lib/cara-mentor-runner/spool root cara-mentor-runner-spool 750" \
  "/var/lib/cara-mentor-runner/spool/requests tg-ingest cara-mentor-runner-spool 2750" \
  "/var/lib/cara-mentor-runner/spool/results cara-mentor-runner cara-mentor-runner-spool 2750" \
  "/var/lib/cara-mentor-runner/scratch cara-mentor-runner cara-mentor-runner-spool 700"
do
  set -- $entry
  actual="$(stat -c '%U %G %a' "$1")"
  [ "$actual" = "$2 $3 $4" ] ||
    fail "bad Mentor ownership/mode on $1: $actual"
done

mentor_request=/var/lib/cara-mentor/spool/requests/.verify-owner
mentor_result=/var/lib/cara-mentor/spool/results/.verify-owner
runner_request=/var/lib/cara-mentor-runner/spool/requests/.verify-owner
runner_result=/var/lib/cara-mentor-runner/spool/results/.verify-owner
runuser -u tg-ingest -g tg-ingest \
  -G cara-worker-spool -G cara-mentor-spool \
  -G cara-mentor-runner-spool -- \
  touch "$mentor_request" "$runner_request"
runuser -u cara-mentor -g cara-mentor-spool -- touch "$mentor_result"
runuser -u cara-mentor-runner -g cara-mentor-runner-spool -- \
  touch "$runner_result"
if runuser -u cara-mentor -g cara-mentor-spool -- \
    rm -f "$mentor_request" 2>/dev/null; then
  fail "Mentor can delete Cara-owned review requests"
fi
if runuser -u cara-mentor-runner -g cara-mentor-runner-spool -- \
    rm -f "$runner_request" 2>/dev/null; then
  fail "Mentor runner can delete Cara-owned test requests"
fi
if runuser -u tg-ingest -g tg-ingest \
    -G cara-worker-spool -G cara-mentor-spool \
    -G cara-mentor-runner-spool -- \
    rm -f "$mentor_result" 2>/dev/null; then
  fail "Cara can delete Mentor-owned results"
fi
if runuser -u tg-ingest -g tg-ingest \
    -G cara-worker-spool -G cara-mentor-spool \
    -G cara-mentor-runner-spool -- \
    rm -f "$runner_result" 2>/dev/null; then
  fail "Cara can delete Mentor-runner-owned results"
fi
runuser -u tg-ingest -g tg-ingest \
  -G cara-worker-spool -G cara-mentor-spool \
  -G cara-mentor-runner-spool -- \
  rm -f "$mentor_request" "$runner_request"
runuser -u cara-mentor -g cara-mentor-spool -- rm -f "$mentor_result"
runuser -u cara-mentor-runner -g cara-mentor-runner-spool -- rm -f "$runner_result"

worker_pid="$(systemctl show cara-worker -p MainPID --value)"
agent_pid="$(systemctl show tg-ingest-agent -p MainPID --value)"
mentor_pid="$(systemctl show cara-mentor -p MainPID --value)"
mentor_runner_pid="$(systemctl show cara-mentor-runner -p MainPID --value)"
[ "${worker_pid:-0}" -gt 1 ] || fail "worker pid is missing"
[ "${agent_pid:-0}" -gt 1 ] || fail "Cara pid is missing"
[ "${mentor_pid:-0}" -gt 1 ] || fail "Mentor pid is missing"
[ "${mentor_runner_pid:-0}" -gt 1 ] || fail "Mentor runner pid is missing"
spool_gid="$(getent group cara-worker-spool | cut -d: -f3)"
grep -Eq "^Groups:.*[[:space:]]${spool_gid}([[:space:]]|$)" \
  "/proc/$agent_pid/status" || fail "live Cara process lacks spool gid"

for protected in \
  /etc/tg-ingest-agent.env \
  /var/lib/tg-ingest-agent/ingest.db \
  /etc/tg-ingest-agent-backup.key \
  /etc/nikki-agent.env \
  /var/lib/nikki-agent/nikki.db \
  /etc/nikki-agent-backup.key \
  /etc/codex-auto-update/telegram.env
do
  nsenter -t "$worker_pid" -m -- runuser -u cara-worker -- \
    test ! -r "$protected" ||
    fail "worker can read protected host path $protected"
done
grep -q '^CapBnd:[[:space:]]*0000000000000000$' "/proc/$worker_pid/status" ||
  fail "live worker retains a capability"
grep -q '^NoNewPrivs:[[:space:]]*1$' "/proc/$worker_pid/status" ||
  fail "live worker lacks no-new-privileges"
grep -q '^CapBnd:[[:space:]]*0000000000000000$' "/proc/$mentor_pid/status" ||
  fail "live Mentor retains a capability"
grep -q '^CapBnd:[[:space:]]*0000000000000000$' \
  "/proc/$mentor_runner_pid/status" ||
  fail "live Mentor runner retains a capability"

tr '\0' '\n' < "/proc/$mentor_pid/environ" |
  grep -Eq '^(TELEGRAM_BOT_TOKEN|FLEET_NOTIFY_|ALLOWED_CHAT_IDS)=' &&
  fail "Mentor inherited production messaging identity"
tr '\0' '\n' < "/proc/$mentor_runner_pid/environ" |
  grep -Eq '^(DO_MODEL_ACCESS_KEY|TELEGRAM_BOT_TOKEN|FLEET_NOTIFY_)=' &&
  fail "Mentor runner inherited a network or messaging credential"

for protected in \
  /etc/tg-ingest-agent.env \
  /var/lib/tg-ingest-agent/ingest.db \
  /etc/tg-ingest-agent-backup.key \
  /root/.ssh
do
  nsenter -t "$mentor_pid" -m -- runuser -u cara-mentor -- \
    test ! -r "$protected" ||
    fail "Mentor can read protected host path $protected"
done
for protected in \
  /etc/tg-ingest-agent.env \
  /etc/cara-mentor.env \
  /var/lib/tg-ingest-agent/ingest.db \
  /etc/tg-ingest-agent-backup.key \
  /root/.ssh
do
  nsenter -t "$mentor_runner_pid" -m -- runuser -u cara-mentor-runner -- \
    test ! -r "$protected" ||
    fail "Mentor runner can read protected host path $protected"
done

if command -v ss >/dev/null 2>&1; then
  if ss -H -lntup 2>/dev/null | grep -q "pid=$worker_pid,"; then
    fail "worker owns a host-network listener"
  fi
  if ss -H -lntup 2>/dev/null | grep -q "pid=$mentor_runner_pid,"; then
    fail "Mentor runner owns a host-network listener"
  fi
fi

[ -r /opt/cara-mentor-source/SOURCE_HASH ] ||
  fail "Mentor source snapshot hash is missing"
expected_source_hash="$(cd /opt/cara-mentor-source && {
  for source_file in $(find . -maxdepth 1 -type f ! -name SOURCE_HASH -printf '%f\n' | sort); do
    printf '%s\0' "$source_file"
    cat "$source_file"
  done
} | sha256sum | cut -d' ' -f1)"
[ "$expected_source_hash" = "$(cat /opt/cara-mentor-source/SOURCE_HASH)" ] ||
  fail "Mentor source snapshot hash mismatch"
[ "$(cat /opt/cara-mentor-source/VERSION)" = \
   "$(cat /opt/tg-ingest-agent/VERSION)" ] ||
  fail "Mentor source snapshot build mismatch"

runuser -u tg-ingest -g tg-ingest -G cara-worker-spool -- \
  /usr/bin/python3 /opt/tg-ingest-agent/verify_task_runtime.py
runuser -u tg-ingest -g tg-ingest \
  -G cara-worker-spool -G cara-mentor-spool \
  -G cara-mentor-runner-spool -- \
  /usr/bin/python3 /opt/tg-ingest-agent/verify_mentor_runtime.py

/usr/bin/python3 /opt/tg-ingest-agent/deployment_notice.py verify-manifest \
  --manifest /opt/tg-ingest-agent/DEPLOYMENT.json \
  --build-file /opt/tg-ingest-agent/VERSION
[ "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='deployment_notifications';")" = "1" ] ||
  fail "deployment notification receipt table is missing"
[ "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db \
  "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='mentor_cycles';")" = "1" ] ||
  fail "Mentor cycle table is missing"
[ "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db 'PRAGMA integrity_check;')" = "ok" ] ||
  fail "SQLite integrity_check failed"
[ -z "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db 'PRAGMA foreign_key_check;')" ] ||
  fail "SQLite foreign_key_check failed"

echo "verify-cara-runtime: ok"
