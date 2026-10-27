# Sourced, never run: the three ephemeral harnesses (scripts/ephemeral_stack.sh,
# scripts/fleet_ephemeral.sh, scripts/fleet_unaware_ephemeral.sh) share the
# container names and ports of elastic-start-local/.env.ephemeral.example under
# their own compose projects, so a stack another one leaked (a SIGKILL skips its
# EXIT trap) makes the next bring-up fail on a name or port clash. D63.
#
# refuse_leaked_siblings <own project>: exit 1 naming the clean target of any
# other harness's project that still has containers. Portable POSIX sh.
refuse_leaked_siblings() {
  for sib in mcp-for-kibana-ephemeral:streams-ephemeral-clean \
             mcp-for-kibana-fleet-ephemeral:fleet-ephemeral-clean \
             mcp-for-kibana-fleet-unaware:fleet-unaware-ephemeral-clean; do
    [ "${sib%%:*}" = "$1" ] && continue
    if [ -n "$(docker compose -p "${sib%%:*}" ps -aq 2>/dev/null)" ]; then
      echo "FAIL: a leaked ${sib%%:*} stack holds the ephemeral container names and ports —" >&2
      echo "      remove it first: make ${sib#*:}" >&2
      exit 1
    fi
  done
}
