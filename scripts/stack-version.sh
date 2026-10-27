# Sourced, never run: the one rule for which Kibana line a stack runs, shared by
# scripts/stack.sh (the dev stack), scripts/ephemeral_stack.sh and
# scripts/fleet_ephemeral.sh (the isolated stacks).
#
# env_with_version <template> <env file>: write <env file> from <template>. An
# ES_LOCAL_VERSION already in the environment (the CI matrix sets it per job)
# replaces the template's pin, and the override is logged; unset, the pin
# stands and nothing is logged. Every compose file reads the version from that
# env file, and the scripts source it, so the override must land IN the file:
# sourcing the plain template reset an exported ES_LOCAL_VERSION to the pin.
# Portable POSIX sh.
env_with_version() {
  if [ -z "${ES_LOCAL_VERSION:-}" ]; then
    cp "$1" "$2"
    return
  fi
  case "$ES_LOCAL_VERSION" in
    *[!0-9.]*) echo "FAIL: ES_LOCAL_VERSION='$ES_LOCAL_VERSION' is not a version like 9.5.4" >&2; return 1 ;;
  esac
  sed "s/^ES_LOCAL_VERSION=.*/ES_LOCAL_VERSION=$ES_LOCAL_VERSION/" "$1" > "$2"
  echo "ES_LOCAL_VERSION=$ES_LOCAL_VERSION (environment override of the $(basename "$1") pin)"
}
