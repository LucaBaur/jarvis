#!/usr/bin/env bash
# Deploy des Jarvis-Orchestrators -> Homeserver ~/jarvis.
# Testet ERST (ruff + pytest) und laedt nur bei gruen hoch.
#
# Uebertragen wird per tar ueber ssh, weil Git Bash unter Windows kein rsync
# mitbringt. Nicht mitgeschickt werden .env, worker.yaml, data/ und alles,
# was in .gitignore steht - Geheimnisse und Datenbank bleiben, wo sie sind.
set -euo pipefail
cd "$(dirname "$0")"

ZIEL_HOST="luca@192.168.8.125"
ZIEL_PFAD="jarvis"
DIENST="jarvis.service"
PY=".venv/Scripts/python.exe"          # Windows-venv fuer die Tests
[ -x "$PY" ] || PY=".venv/bin/python"  # falls von Linux aus deployt wird

echo "== 1/4 Lint =="
"$PY" -m ruff check .

echo "== 2/4 Tests =="
"$PY" -m pytest -q

echo "== 3/4 Uebertragen nach $ZIEL_HOST:~/$ZIEL_PFAD =="
ssh "$ZIEL_HOST" "mkdir -p ~/$ZIEL_PFAD"
tar czf - \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  jarvis worker docs deploy pyproject.toml ruff.toml .env.example README.md CHANGELOG.md \
  | ssh "$ZIEL_HOST" "tar xzf - -C ~/$ZIEL_PFAD"

echo "== 4/4 Abhaengigkeiten und Dienst =="
ssh "$ZIEL_HOST" bash -s <<'REMOTE'
set -euo pipefail
cd ~/jarvis

if [ ! -d .venv ]; then
  echo "  venv anlegen"
  # Auf diesem Server fehlt python3.14-venv, deshalb kein ensurepip. Genau
  # dieselbe Huerde gab es bei ~/anytype-sync; dort wurde --without-pip
  # verwendet und pip danach von Hand geholt. Sauberer waere
  # "sudo apt install python3.14-venv", das braucht aber ein Passwort.
  python3 -m venv .venv 2>/dev/null || {
    rm -rf .venv
    python3 -m venv --without-pip .venv
  }
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "  pip nachziehen (offizielles get-pip von bootstrap.pypa.io)"
  curl -sSf https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
  .venv/bin/python /tmp/get-pip.py -q
  rm -f /tmp/get-pip.py
fi

.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e .

if [ ! -f .env ]; then
  cp .env.example .env
  TOKEN=$(.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))")
  sed -i "s|^JARVIS_TOKEN=.*|JARVIS_TOKEN=$TOKEN|" .env
  echo "  .env angelegt, Token erzeugt:"
  echo "  $TOKEN"
  echo "  (steht in ~/jarvis/.env, kommt so in die worker.yaml)"
fi

export XDG_RUNTIME_DIR=/run/user/$(id -u)
if systemctl --user list-unit-files jarvis.service >/dev/null 2>&1 \
   && systemctl --user is-enabled jarvis.service >/dev/null 2>&1; then
  systemctl --user restart jarvis.service
  sleep 2
  systemctl --user is-active jarvis.service
else
  echo "  Dienst noch nicht eingerichtet - siehe docs/SETUP-SERVER.md"
fi
REMOTE

echo
echo "== Fertig =="
echo "   http://192.168.8.125:8400                    im Heimnetz"
echo "   https://homeserver.tail112520.ts.net:10000   ueber Tailscale, noetig fuers Mikrofon"
echo
echo "   Token des Servers (nicht das aus der lokalen .env):"
ssh "$ZIEL_HOST" "grep '^JARVIS_TOKEN=' ~/$ZIEL_PFAD/.env | cut -d= -f2 | sed 's/^/   /'"
