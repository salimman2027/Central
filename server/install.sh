#!/usr/bin/env bash
# تركيب / تحديث لوحة مكالمات NQ على سيرفر السنترال (FreePBX على Debian)
# بيتشغّل من GitHub Actions أوتوماتيك، أو يدويًا:  sudo bash install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP=/opt/nq-calls
CONF=/etc/nq-calls
DATA=/var/lib/nq-calls
HOST=3.89.245.93.sslip.io
RUN_USER=asterisk

# أسرار جاية من GitHub (بتتمسح بعد القراية)
if [[ -f /tmp/nq-secrets.env ]]; then set -a; . /tmp/nq-secrets.env; set +a; rm -f /tmp/nq-secrets.env; fi

# تركيب يدوي من غير GitHub secrets: يسأل عن الباسورد والمفتاح لو مش موجودين
if [[ -t 0 ]]; then
  if [[ -z "${NQ_DASH_PASSWORD:-}" && ! -f /etc/nq-calls/htpasswd ]]; then
    read -rsp "اختار باسورد للوحة المكالمات (user: admin): " NQ_DASH_PASSWORD; echo
  fi
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]] && ! grep -q '^ANTHROPIC_API_KEY=.\+' /etc/nq-calls/env 2>/dev/null; then
    read -rsp "الصق مفتاح Anthropic API (أو Enter للتخطي): " ANTHROPIC_API_KEY; echo
  fi
fi

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq python3-venv python3-pip apache2-utils >/dev/null

echo "== app files"
mkdir -p "$APP" "$CONF" "$DATA/analysis"
cp -r "$SRC"/app.py "$SRC"/analyzer.py "$SRC"/requirements.txt "$SRC"/static "$APP"/
[[ -f "$CONF/reps.json" ]] || cp "$SRC/reps.example.json" "$CONF/reps.json"
chown -R "$RUN_USER:$RUN_USER" "$DATA"

# نسخة اللابتوب من التليفون (نفس صفحة التطبيق) على https://HOST/phone/
if [[ -d "$SRC/../app/src/main/assets" ]]; then
  rm -rf "$APP/phone"; cp -r "$SRC/../app/src/main/assets" "$APP/phone"
  cp "$APP/phone/phone.html" "$APP/phone/index.html"
fi

echo "== python env"
[[ -x "$APP/venv/bin/python" ]] || python3 -m venv "$APP/venv"
NEWHASH=$(sha256sum "$APP/requirements.txt" | cut -d' ' -f1)
if [[ "$(cat "$APP/.req-hash" 2>/dev/null)" != "$NEWHASH" ]]; then
  "$APP/venv/bin/pip" install -q --upgrade pip
  "$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"
  echo "$NEWHASH" > "$APP/.req-hash"
fi

echo "== settings"
touch "$CONF/env"; chmod 600 "$CONF/env"
setkv() { grep -q "^$1=" "$CONF/env" && sed -i "s|^$1=.*|$1=$2|" "$CONF/env" || echo "$1=$2" >> "$CONF/env"; }
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
grep -q '^NQ_WHISPER_MODEL=' "$CONF/env" || setkv NQ_WHISPER_MODEL "$([[ $MEM_GB -ge 4 ]] && echo small || echo base)"
setkv NQ_AI_OUT "$DATA/analysis"
setkv HF_HOME "$DATA/models"
[[ -n "${ANTHROPIC_API_KEY:-}" ]] && setkv ANTHROPIC_API_KEY "$ANTHROPIC_API_KEY"
chown root:"$RUN_USER" "$CONF/env"; chmod 640 "$CONF/env"

echo "== service"
cat > /etc/systemd/system/nq-calls.service <<EOF
[Unit]
Description=NQ Calls dashboard
After=network.target mariadb.service

[Service]
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP
EnvironmentFile=$CONF/env
ExecStart=$APP/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8090 --root-path /calls
Restart=always
Nice=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q nq-calls
systemctl restart nq-calls

# المرحلة 2 (تحليل تسجيلات المكالمات): NQ_PHASE2=1 bash install.sh  (بيتفتكر بعد كده)
[[ "${NQ_PHASE2:-}" == 1 ]] && touch "$CONF/phase2"
[[ -f "$CONF/phase2" ]] && NQ_PHASE2=1
if [[ "${NQ_PHASE2:-}" == 1 ]]; then
  echo "== phase 2: call-recording analysis"
  apt-get install -y -qq ffmpeg >/dev/null
  "$APP/venv/bin/pip" install -q -r "$SRC/requirements-phase2.txt"
  cat > /etc/cron.d/nq-calls <<EOF
0 3 * * 6 $RUN_USER set -a; . $CONF/env; nice -n 19 $APP/venv/bin/python $APP/analyzer.py --last-week >> $DATA/weekly.log 2>&1
EOF
fi

echo "== web (https://$HOST/calls)"
if [[ -n "${NQ_DASH_PASSWORD:-}" ]]; then
  htpasswd -bcB "$CONF/htpasswd" admin "$NQ_DASH_PASSWORD" 2>/dev/null
fi
[[ -f "$CONF/htpasswd" ]] || { echo "!! no dashboard password set (NQ_DASH_PASSWORD)"; exit 1; }
# FreePBX بيشغّل Apache بيوزر asterisk مش www-data
WEB_USER=$(ps -o user= -C apache2 2>/dev/null | grep -v '^root' | head -1 || true)
WEB_GROUP=$( [[ -n "$WEB_USER" ]] && id -gn "$WEB_USER" 2>/dev/null || true )
WEB_GROUP=${WEB_GROUP:-www-data}
echo "   apache group: $WEB_GROUP"
chown root:"$WEB_GROUP" "$CONF/htpasswd"; chmod 640 "$CONF/htpasswd"

CERT=/etc/asterisk/keys/integration/certificate.pem
KEY=/etc/asterisk/keys/integration/webserver.key
[[ -f "/etc/asterisk/keys/$HOST.crt" ]] && { CERT="/etc/asterisk/keys/$HOST.crt"; KEY="/etc/asterisk/keys/$HOST.key"; }
[[ -f "/etc/asterisk/keys/$HOST.pem" ]] && CERT="/etc/asterisk/keys/$HOST.pem"

a2enmod -q ssl proxy proxy_http proxy_wstunnel headers rewrite >/dev/null
a2dissite -q nq-wss >/dev/null 2>&1 || true
cat > /etc/apache2/sites-available/nq-calls.conf <<EOF
<VirtualHost *:443>
  ServerName $HOST
  SSLEngine on
  SSLCertificateFile    $CERT
  SSLCertificateKeyFile $KEY

  # تليفون المتصفح على 443 (لو شبكة بتقفل 8089)
  SSLProxyEngine on
  SSLProxyVerify none
  SSLProxyCheckPeerName off
  SSLProxyCheckPeerCN off
  ProxyPass        /ws      wss://127.0.0.1:8089/ws
  ProxyPassReverse /ws      wss://127.0.0.1:8089/ws

  # التطبيق بيبعت ملخص المكالمة هنا (بيتحقق برقم التحويلة وباسوردها)
  ProxyPass        /calls-app/notes http://127.0.0.1:8090/app/notes
  ProxyPassReverse /calls-app/notes http://127.0.0.1:8090/app/notes
  ProxyPass        /calls-app/insight http://127.0.0.1:8090/app/insight
  ProxyPassReverse /calls-app/insight http://127.0.0.1:8090/app/insight

  # التليفون من اللابتوب
  RedirectMatch ^/phone\$ /phone/
  Alias /phone/ $APP/phone/
  <Directory $APP/phone>
    Require all granted
    Header set Cache-Control "no-cache"
  </Directory>

  # لوحة المكالمات
  RedirectMatch ^/calls\$ /calls/
  <Location /calls/>
    AuthType Basic
    AuthName "NQ Calls"
    AuthUserFile $CONF/htpasswd
    Require valid-user
    ProxyPass        http://127.0.0.1:8090/
    ProxyPassReverse http://127.0.0.1:8090/
  </Location>
</VirtualHost>
EOF
grep -qE '^\s*Listen\s+443' /etc/apache2/ports.conf /etc/apache2/conf-enabled/*.conf 2>/dev/null || echo "Listen 443" >> /etc/apache2/ports.conf
a2ensite -q nq-calls >/dev/null
apachectl configtest
systemctl reload apache2

# أمر تحديث سريع: sudo nq-update
cat > /usr/local/bin/nq-update <<'UPD'
#!/bin/bash
set -e
cd /tmp && rm -rf Central && git clone -q https://github.com/salimman2027/Central && bash Central/server/install.sh
UPD
chmod 755 /usr/local/bin/nq-update

echo "== check"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8090/api/config >/dev/null; then break; fi; sleep 2
done
curl -fsS http://127.0.0.1:8090/api/config >/dev/null && echo "service OK"
D=$(date +%F)
curl -sS "http://127.0.0.1:8090/api/calls?from=$D&to=$D" | head -c 400; echo
journalctl -u nq-calls -n 8 --no-pager 2>/dev/null | grep -iE "error|exception" | tail -3 || true
echo "DONE  ->  https://$HOST/calls/   (user: admin)"
