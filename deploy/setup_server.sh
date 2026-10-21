#!/usr/bin/env bash
# Richtet den Bot auf einem frischen Ubuntu-Server (24.04) ein.
# Aufruf auf dem Server, im Ordner mit dem Code:   bash deploy/setup_server.sh
# Mehrfach ausführbar: Ein zweiter Lauf aktualisiert Code und Dienste.
set -euo pipefail

APP=/opt/trading-bot
SRC="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE=/etc/tradingbot.env

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte als root ausführen." >&2
  exit 1
fi

echo "==> Pakete installieren"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3 python3-venv caddy ufw rsync

echo "==> Benutzer und Code"
id -u bot >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/bot --shell /usr/sbin/nologin bot
mkdir -p "$APP"
if [ "$SRC" != "$APP" ]; then
  rsync -a --delete --exclude .venv --exclude data --exclude config.yaml --exclude bot.log "$SRC/" "$APP/"
  # Übungskonto und Kursdaten nur beim ersten Mal übernehmen, nie überschreiben
  if [ -d "$SRC/data" ] && [ ! -d "$APP/data" ]; then cp -a "$SRC/data" "$APP/data"; fi
  if [ -f "$SRC/config.yaml" ] && [ ! -f "$APP/config.yaml" ]; then cp "$SRC/config.yaml" "$APP/config.yaml"; fi
fi
[ -f "$APP/config.yaml" ] || cp "$APP/config.example.yaml" "$APP/config.yaml"
mkdir -p "$APP/data"
chown -R bot:bot "$APP"

echo "==> Python-Umgebung"
sudo -u bot python3 -m venv "$APP/.venv"
sudo -u bot "$APP/.venv/bin/pip" install -q --upgrade pip
sudo -u bot "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "==> Dashboard-Passwort"
if [ ! -f "$ENV_FILE" ]; then
  PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  printf 'DASHBOARD_PASSWORD=%s\n' "$PASSWORD" > "$ENV_FILE"
fi
chown root:bot "$ENV_FILE"
chmod 640 "$ENV_FILE"

echo "==> Dienste"
cp "$APP/deploy/tradingbot.service" /etc/systemd/system/
cp "$APP/deploy/tradingbot-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tradingbot tradingbot-dashboard
systemctl restart tradingbot tradingbot-dashboard

echo "==> HTTPS über sslip.io"
IP="$(curl -4 -s https://ifconfig.me || hostname -I | awk '{print $1}')"
HOST="$(echo "$IP" | tr . -).sslip.io"
cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
    reverse_proxy 127.0.0.1:8080
}
CADDY
systemctl reload caddy || systemctl restart caddy

echo "==> Firewall"
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

# shellcheck disable=SC1090
. "$ENV_FILE"
echo
echo "Fertig! Der Bot läuft rund um die Uhr."
echo "Dashboard:  https://$HOST"
echo "Benutzer:   beliebig (z. B. noel)"
echo "Passwort:   $DASHBOARD_PASSWORD"
echo
echo "Nützlich:   journalctl -u tradingbot -f     (Live-Protokoll, Strg+C beendet)"
echo "            systemctl restart tradingbot    (nach Änderungen an config.yaml)"
