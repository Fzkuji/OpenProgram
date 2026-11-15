#!/bin/sh
# Installed as <root>/bin/openprogram. `versions` runs outside every version,
# so it works when the current one is broken; everything else uses the current
# version's Python and carries no guard, because a failing command shows its
# error to the person who typed it.
set -eu
self="$0"
while [ -h "$self" ]; do
  link="$(readlink "$self")"
  case "$link" in
    /*) self="$link" ;;
    *) self="$(dirname "$self")/$link" ;;
  esac
done
root="$(cd "$(dirname "$self")/.." && pwd)"
value() { sed -n "s/^$1=//p" "$root/current" | head -n 1; }
base="$(value base 2>/dev/null || true)"
base_python="$root/bases/$base/python/bin/python3"
if [ "${1:-}" = versions ]; then
  shift
  if [ -z "$base" ] || [ ! -x "$base_python" ]; then
    base_python="$(ls -d "$root"/bases/*/python/bin/python3 2>/dev/null | head -n 1)"
  fi
  [ -n "$base_python" ] || { echo "openprogram: no base Python found under $root/bases" >&2; exit 1; }
  exec "$base_python" -I -B "$root/bootstrap/versions.py" --root "$root" "$@"
fi
version="$(value version)"
exec "$root/versions/$version/venv/bin/python" -I -B -m openprogram "$@"
