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
case " $agent_groups " in
  *" cara-worker-spool "*) ;;
  *) fail "Cara service lacks its narrow spool group" ;;
esac

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

worker_pid="$(systemctl show cara-worker -p MainPID --value)"
agent_pid="$(systemctl show tg-ingest-agent -p MainPID --value)"
[ "${worker_pid:-0}" -gt 1 ] || fail "worker pid is missing"
[ "${agent_pid:-0}" -gt 1 ] || fail "Cara pid is missing"
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

if command -v ss >/dev/null 2>&1; then
  if ss -H -lntup 2>/dev/null | grep -q "pid=$worker_pid,"; then
    fail "worker owns a host-network listener"
  fi
fi

runuser -u tg-ingest -g tg-ingest -G cara-worker-spool -- \
  /usr/bin/python3 /opt/tg-ingest-agent/verify_task_runtime.py

[ "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db 'PRAGMA integrity_check;')" = "ok" ] ||
  fail "SQLite integrity_check failed"
[ -z "$(sqlite3 /var/lib/tg-ingest-agent/ingest.db 'PRAGMA foreign_key_check;')" ] ||
  fail "SQLite foreign_key_check failed"

echo "verify-cara-runtime: ok"
