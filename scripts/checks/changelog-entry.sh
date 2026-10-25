#!/bin/sh
# The DoD gate's `changelog_entry` check (D54), also a checks.yml step on PRs.
#
# A change that touches anything outside the exemption list below must add a
# non-blank line to CHANGELOG.md under `## [Unreleased]` (or under a release
# section the same change adds — a release cut moves entries there). The change
# is everything since the base: committed, staged, unstaged and untracked (not
# ignored) files. The base is `git merge-base HEAD origin/main`, falling back to
# the latest `v*` tag; with neither, the check fails closed naming the fetch.
#
# Prints one line (the reason) and exits 0 (GO) or 1 (NO-GO).
# Portable POSIX sh; needs only git and awk.
set -u

cd "$(git rev-parse --show-toplevel)" || exit 2

# Paths that never need an entry: committed run records (data, not behaviour),
# working plans, and lock-file-only dependency bumps. Keep this list short; a
# path here is a promise that changing it changes nothing a user sees.
exempt() {
  case "$1" in
    CHANGELOG.md | scripts/experiment/runs/* | docs/plans/* | uv.lock) return 0 ;;
  esac
  return 1
}

if git rev-parse --verify -q refs/remotes/origin/main >/dev/null; then
  if ! base=$(git merge-base HEAD refs/remotes/origin/main 2>/dev/null); then
    echo "HEAD shares no history with origin/main (a shallow clone?) — run: git fetch --unshallow origin main (or git fetch origin main)"
    exit 1
  fi
  base_label="merge-base with origin/main $(git rev-parse --short "$base")"
elif tag=$(git describe --tags --abbrev=0 --match 'v*' HEAD 2>/dev/null); then
  base=$(git rev-parse "$tag^{commit}")
  base_label="latest tag $tag (no origin/main)"
else
  echo "no base to diff against: no origin/main and no v* tag — run: git fetch origin main"
  exit 1
fi

changed=$( { git -c core.quotePath=false diff --name-only "$base" --; git ls-files -o --exclude-standard; } | sort -u)
need=
n=0
set -f                       # no globbing, split on newlines only: paths may hold spaces
old_ifs=$IFS; IFS='
'
for p in $changed; do
  if ! exempt "$p"; then
    n=$((n + 1))
    [ "$n" -le 5 ] && need="${need:+$need, }$p"
  fi
done
IFS=$old_ifs; set +f

if [ "$n" -eq 0 ]; then
  echo "no change outside the exempt paths since the $base_label"
  exit 0
fi
[ "$n" -gt 5 ] && need="$need, ... ($n paths)"

if [ ! -f CHANGELOG.md ]; then
  echo "CHANGELOG.md is missing — restore it and add an entry under ## [Unreleased] in CHANGELOG.md"
  exit 1
fi

# Line numbers (in the working-tree CHANGELOG.md) of the lines this change
# added, from the -U0 hunk headers `@@ -a,b +c,d @@` (d omitted means 1).
added=$(git diff -U0 --no-color "$base" -- CHANGELOG.md | awk '
  /^@@ / {
    split($3, r, ","); start = substr(r[1], 2) + 0; cnt = (2 in r) ? r[2] + 0 : 1
    for (i = 0; i < cnt; i++) print start + i
  }')

entries=$(printf '%s\n' "$added" | awk '
  FNR == NR { if ($0 != "") add[$0] = 1; next }
  /^## / {
    if ($0 ~ /^## \[?[Uu]nreleased/) sec = "unreleased"
    else if ($0 ~ /^## \[?[0-9]+\.[0-9]+\.[0-9]+/ && (FNR in add)) { sec = "new-release"; n++ }
    else sec = "other"
    next
  }
  /^# / { sec = "other"; next }
  (FNR in add) && $0 ~ /[^[:space:]]/ && (sec == "unreleased" || sec == "new-release") { n++ }
  END { print n + 0 }
' - CHANGELOG.md)

if [ "$entries" -ge 1 ]; then
  echo "$entries line(s) added under [Unreleased] or a new release section; $n changed path(s) since the $base_label"
  exit 0
fi
echo "$n changed path(s) since the $base_label ($need) but no line added under ## [Unreleased] — add an entry under ## [Unreleased] in CHANGELOG.md"
exit 1
