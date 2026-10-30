#!/bin/sh
# The DoD gate's `changelog_entry` check (D54), also a checks.yml step on PRs.
#
# A change that touches anything outside the exemption list below must make
# CHANGELOG.md gain an entry under `## [Unreleased]`: more entry lines there than
# at the base, at least one of them text the base changelog does not hold (so a
# reworded, re-spaced or moved entry is not one), where an entry line is any
# non-blank line that is not a heading. A release cut — a `## [x.y.z]` heading
# whose version the base does not have — counts as the entry. The change
# is everything since the base: committed, staged, unstaged and untracked (not
# ignored) files. The base is `git merge-base HEAD origin/main`, falling back to
# the latest `v*` tag; with neither, the check fails closed naming the fetch.
#
# Prints one line (the reason) and exits 0 (GO) or 1 (NO-GO).
# Portable POSIX sh; needs only git and awk.
set -u

cd "$(git rev-parse --show-toplevel)" || exit 2

# Paths that never need an entry: committed run records (data, not behaviour)
# and the working plans under .claude/plans/. Keep this list short; a path here
# is a promise that changing it changes nothing a user sees. uv.lock is not on
# it: the image installs from it, and a lock-file-only CVE fix gets an entry.
exempt() {
  case "$1" in
    CHANGELOG.md | scripts/experiment/runs/* | .claude/plans/*) return 0 ;;
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

# Renames off, so a file moved into an exempt path still counts at its old one.
# A git failure here is NO-GO, never an empty (passing) list.
if ! diffed=$(git -c core.quotePath=false diff --no-renames --name-only "$base" --) \
  || ! untracked=$(git -c core.quotePath=false ls-files -o --exclude-standard); then
  echo "could not list the changed paths since the $base_label (git failed) — repair the clone (git fsck; git fetch origin main) and re-run"
  exit 1
fi
changed=$(printf '%s\n%s\n' "$diffed" "$untracked" | sort -u)
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

# The base changelog (empty when the base has none). A missing path is the only
# failure tolerated; anything else is NO-GO.
if git cat-file -e "$base:CHANGELOG.md" 2>/dev/null; then
  if ! base_log=$(git show "$base:CHANGELOG.md"); then
    echo "could not read CHANGELOG.md at the $base_label (git failed) — repair the clone (git fsck; git fetch origin main) and re-run"
    exit 1
  fi
else
  base_log=
fi

# Pass 1 (the base): every line's text with trailing blanks dropped, the release
# versions, and the entry-line count under [Unreleased]. Pass 2 (the working
# tree): the same count, how many of those lines are text the base lacks, and
# release headings whose version is new.
result=$(printf '%s\n' "$base_log" | awk '
  function rtrim(s) { sub(/[[:space:]]+$/, "", s); return s }
  function version(s) { sub(/^## \[?/, "", s); sub(/[^0-9.].*$/, "", s); return s }
  function section(s) {
    if (s ~ /^## \[?[Uu]nreleased/) return "unreleased"
    if (s ~ /^## \[?[0-9]+\.[0-9]+\.[0-9]+/) return "release"
    return "other"
  }
  function entry(s) { return s ~ /[^[:space:]]/ && s !~ /^#/ }
  FNR == NR {
    t = rtrim($0); seen[t] = 1
    if (t ~ /^#/) { sec = (t ~ /^## /) ? section(t) : (t ~ /^# / ? "other" : sec); if (sec == "release") ver[version(t)] = 1; next }
    if (sec == "unreleased" && entry(t)) old++
    next
  }
  FNR == 1 { sec = "" }
  {
    t = rtrim($0)
    if (t ~ /^#/) {
      if (t ~ /^## /) { sec = section(t); if (sec == "release" && !(version(t) in ver)) cut++ }
      else if (t ~ /^# /) sec = "other"
      next
    }
    if (sec == "unreleased" && entry(t)) { now++; if (!(t in seen)) fresh++ }
  }
  END { printf "%d %d %d %d\n", old, now, fresh, cut }
' - CHANGELOG.md)
set -- $result
old=$1 now=$2 fresh=$3 cut=$4

if [ "$cut" -ge 1 ]; then
  echo "release cut: $cut new release section(s) in CHANGELOG.md; $n changed path(s) since the $base_label"
  exit 0
fi
if [ "$now" -gt "$old" ] && [ "$fresh" -ge 1 ]; then
  echo "$((now - old)) entry line(s) gained under [Unreleased] ($fresh new); $n changed path(s) since the $base_label"
  exit 0
fi
echo "$n changed path(s) since the $base_label ($need) but CHANGELOG.md gained no entry under ## [Unreleased] ($old entry line(s) at the base, $now now, $fresh new) — add an entry under ## [Unreleased] in CHANGELOG.md"
exit 1
