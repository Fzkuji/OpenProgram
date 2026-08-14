#!/usr/bin/env sh
set -eu

UV_VERSION="0.11.16"
PYTHON_VERSION="3.12.10"
OPENPROGRAM_VERSION="${OPENPROGRAM_VERSION:-0.6.1}"
state_root="${OPENPROGRAM_STATE_DIR:-$HOME/.openprogram}"
runtime_root="$state_root/runtime/cli"
release_dir="$runtime_root/releases/$OPENPROGRAM_VERSION"
uv_dir="$runtime_root/bootstrap/uv/$UV_VERSION"
uv_bin="$uv_dir/uv"

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) printf 'OpenProgram release installer supports macOS and Linux only.\n' >&2; exit 1 ;;
esac

if [ ! -x "$uv_bin" ]; then
  mkdir -p "$uv_dir"
  installer_url="https://releases.astral.sh/github/uv/releases/download/$UV_VERSION/uv-installer.sh"
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -LsSf "$installer_url" \
      | env UV_UNMANAGED_INSTALL="$uv_dir" sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$installer_url" | env UV_UNMANAGED_INSTALL="$uv_dir" sh
  else
    printf 'curl or wget is required to download uv.\n' >&2
    exit 1
  fi
fi

mkdir -p "$release_dir/python" "$release_dir/bin" "$runtime_root/releases"
UV_PYTHON_INSTALL_DIR="$release_dir/python" \
  "$uv_bin" python install "$PYTHON_VERSION" --install-dir "$release_dir/python" --no-bin
python_bin="$(UV_PYTHON_INSTALL_DIR="$release_dir/python" \
  "$uv_bin" python find --managed-python "$PYTHON_VERSION")"
if [ -n "${OPENPROGRAM_WHEEL:-}" ]; then
  case "$OPENPROGRAM_WHEEL" in
    /*.whl) test -f "$OPENPROGRAM_WHEEL" || { printf 'OPENPROGRAM_WHEEL not found.\n' >&2; exit 1; } ;;
    *) printf 'OPENPROGRAM_WHEEL must be an absolute wheel path.\n' >&2; exit 1 ;;
  esac
  package_spec="$OPENPROGRAM_WHEEL"
else
  package_spec="openprogram==${OPENPROGRAM_VERSION}"
fi
"$uv_bin" pip install --python "$python_bin" --strict --break-system-packages \
  "$package_spec"
"$python_bin" -I -m openprogram --version

probe_port="$((20000 + $$ % 10000))"
probe_home="$release_dir/.probe-home-$$"
mkdir -p "$probe_home"
probe_active=1
stop_probe() {
  if [ "$probe_active" = 1 ]; then
    HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
      "$python_bin" -I -B -m openprogram worker stop >/dev/null 2>&1 || true
  fi
  rm -rf "$probe_home"
}
trap stop_probe EXIT HUP INT TERM
HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
  "$python_bin" -I -B -m openprogram worker start
"$python_bin" -I -B - "$probe_port" <<'PY'
import json
import sys
import time
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}/healthz"
for attempt in range(120):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            payload = json.load(response)
        if payload.get("status") == "ok":
            break
    except Exception:
        if attempt == 119:
            raise
        time.sleep(0.25)
else:
    raise RuntimeError("OpenProgram worker did not become healthy")
PY
HOME="$probe_home" OPENPROGRAM_WEB_PORT="$probe_port" \
  "$python_bin" -I -B -m openprogram worker stop
probe_active=0
rm -rf "$probe_home"
trap - EXIT HUP INT TERM

ln -sfn "$python_bin" "$release_dir/bin/python"
next_link="$runtime_root/.current-$OPENPROGRAM_VERSION-$$"
ln -s "$release_dir" "$next_link"
mv -f "$next_link" "$runtime_root/current"

launcher_dir="${OPENPROGRAM_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$launcher_dir"
launcher_tmp="$launcher_dir/.openprogram-$$"
cat > "$launcher_tmp" <<EOF
#!/bin/sh
exec "$runtime_root/current/bin/python" -I -m openprogram "\$@"
EOF
chmod 0755 "$launcher_tmp"
mv -f "$launcher_tmp" "$launcher_dir/openprogram"

printf 'OpenProgram %s installed.\n' "$OPENPROGRAM_VERSION"
printf 'Executable: %s/openprogram\n' "$launcher_dir"
printf 'Runtime: %s\n' "$release_dir"
