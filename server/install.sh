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
cp -r "$SRC"/app.py "$SRC"/files_api.py "$SRC"/rubric.py "$SRC"/taxonomy.py "$SRC"/appointments.py "$SRC"/ownership.py "$SRC"/rep_portal.py "$SRC"/training.py "$SRC"/ops.py "$SRC"/targets.py "$SRC"/todo.py "$SRC"/goals.py "$SRC"/workcal.py "$SRC"/wa.py "$SRC"/eng.py "$SRC"/wainbox.py "$SRC"/analyzer.py "$SRC"/requirements.txt "$SRC"/static "$APP"/
[[ -f "$CONF/reps.json" ]] || cp "$SRC/reps.example.json" "$CONF/reps.json"
VER="$(cat "$SRC/../VERSION" 2>/dev/null || echo "?")"
sed -i "s/__VER__/$VER/g" "$APP/static/index.html"
echo "   version: $VER"
chown -R "$RUN_USER:$RUN_USER" "$DATA"

# نسخة اللابتوب من التليفون (نفس صفحة التطبيق) على https://HOST/phone/
if [[ -d "$SRC/../app/src/main/assets" ]]; then
  rm -rf "$APP/phone"; cp -r "$SRC/../app/src/main/assets" "$APP/phone"
  sed -i "s/__VER__/$(cat "$SRC/../VERSION" 2>/dev/null)/g" "$APP/phone/phone.html"
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
MEM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)
CUR_MODEL=$(sed -n 's/^NQ_WHISPER_MODEL=//p' "$CONF/env")
# النموذج الأدق اللي الرام يستحمله (المختار يدويًا غير base/small بيفضل زي ما هو)
if [[ -z "$CUR_MODEL" || "$CUR_MODEL" == base || "$CUR_MODEL" == small ]]; then
  setkv NQ_WHISPER_MODEL "$([[ $MEM_MB -ge 2800 ]] && echo large-v3-turbo || echo small)"
fi
echo "   RAM: ${MEM_MB}MB — speech model: $(sed -n 's/^NQ_WHISPER_MODEL=//p' "$CONF/env")"
setkv NQ_AI_OUT "$DATA/analysis"
setkv HF_HOME "$DATA/models"
ANTHROPIC_API_KEY=$(printf '%s' "${ANTHROPIC_API_KEY:-}" | tr -d '[:space:]')
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
# لو faster-whisper متركّب قبل كده يبقى المرحلة 2 شغالة
"$APP/venv/bin/python" -c "import faster_whisper" 2>/dev/null && { NQ_PHASE2=1; touch "$CONF/phase2"; }
if [[ "${NQ_PHASE2:-}" == 1 ]]; then
  echo "== phase 2: call-recording analysis"
  apt-get install -y -qq ffmpeg >/dev/null
  # مساحة swap احتياطي عشان نموذج الصوت ميزاحمش السنترال على الرام
  if ! swapon --show | grep -q . && [[ $MEM_MB -lt 6000 ]]; then
    if fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile; then
      grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
      echo "   swap: 2G added"
    fi
  fi
  "$APP/venv/bin/pip" install -q -r "$SRC/requirements-phase2.txt"
  cat > /etc/cron.d/nq-calls <<EOF
30 1 * * * $RUN_USER set -a; . $CONF/env; nice -n 19 $APP/venv/bin/python $APP/analyzer.py --yesterday >> $DATA/nightly.log 2>&1
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
chown root:"$WEB_GROUP" "$CONF/htpasswd"; chmod 660 "$CONF/htpasswd"   # السيرفر بيضيف يوزرات قادة الفرق

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
  # مشاركة الملفات بين الموظفين + اللينكات العامة
  ProxyPass        /calls-app/files http://127.0.0.1:8090/app/files timeout=600
  ProxyPassReverse /calls-app/files http://127.0.0.1:8090/app/files
  ProxyPass        /calls-app/f/ http://127.0.0.1:8090/app/f/
  ProxyPassReverse /calls-app/f/ http://127.0.0.1:8090/app/f/
  ProxyPass        /calls-app/upload http://127.0.0.1:8090/app/upload timeout=300
  ProxyPassReverse /calls-app/upload http://127.0.0.1:8090/app/upload
  ProxyPass        /calls-app/insight http://127.0.0.1:8090/app/insight
  ProxyPassReverse /calls-app/insight http://127.0.0.1:8090/app/insight
  ProxyPass        /calls-app/owner http://127.0.0.1:8090/app/owner
  ProxyPassReverse /calls-app/owner http://127.0.0.1:8090/app/owner
  ProxyPass        /calls-app/lookup http://127.0.0.1:8090/app/lookup
  ProxyPassReverse /calls-app/lookup http://127.0.0.1:8090/app/lookup
  ProxyPass        /calls-app/login http://127.0.0.1:8090/app/login
  ProxyPassReverse /calls-app/login http://127.0.0.1:8090/app/login
  ProxyPass        /calls-app/me http://127.0.0.1:8090/app/me
  ProxyPassReverse /calls-app/me http://127.0.0.1:8090/app/me
  ProxyPass        /calls-app/history http://127.0.0.1:8090/app/history
  ProxyPassReverse /calls-app/history http://127.0.0.1:8090/app/history
  ProxyPass        /calls-app/inbox http://127.0.0.1:8090/app/inbox
  ProxyPassReverse /calls-app/inbox http://127.0.0.1:8090/app/inbox
  ProxyPass        /calls-app/targets http://127.0.0.1:8090/app/targets
  ProxyPassReverse /calls-app/targets http://127.0.0.1:8090/app/targets
  ProxyPass        /calls-app/todo http://127.0.0.1:8090/app/todo
  ProxyPassReverse /calls-app/todo http://127.0.0.1:8090/app/todo
  ProxyPass        /calls-app/goals http://127.0.0.1:8090/app/goals
  ProxyPassReverse /calls-app/goals http://127.0.0.1:8090/app/goals
  ProxyPass        /calls-app/wa http://127.0.0.1:8090/app/wa
  ProxyPassReverse /calls-app/wa http://127.0.0.1:8090/app/wa
  ProxyPass        /calls-app/training http://127.0.0.1:8090/app/training
  ProxyPassReverse /calls-app/training http://127.0.0.1:8090/app/training
  ProxyPass        /calls-app/eng http://127.0.0.1:8090/app/eng
  ProxyPassReverse /calls-app/eng http://127.0.0.1:8090/app/eng
  ProxyPass        /calls-app/partner-check http://127.0.0.1:8090/app/partner-check
  ProxyPassReverse /calls-app/partner-check http://127.0.0.1:8090/app/partner-check
  ProxyPass        /calls-app/password http://127.0.0.1:8090/app/password
  ProxyPassReverse /calls-app/password http://127.0.0.1:8090/app/password

  # التليفون من اللابتوب
  RedirectMatch ^/phone\$ /phone/
  Alias /phone/ $APP/phone/
  <Directory $APP/phone>
    Require all granted
    Header set Cache-Control "no-cache"
    AddType application/manifest+json .webmanifest
  </Directory>

  # لوحة المكالمات
  RedirectMatch ^/calls\$ /calls/
  <Location /calls/>
    # الدخول بصفحة تسجيل دخول جوه اللوحة (جلسة + زرار خروج) — اليوزرات في $CONF/htpasswd
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

echo "== backup (nightly 2:00)"
command -v rclone >/dev/null || apt-get install -y -qq rclone >/dev/null 2>&1 || true
command -v aws >/dev/null || apt-get install -y -qq awscli >/dev/null 2>&1 || "$APP/venv/bin/pip" install -q awscli >/dev/null 2>&1 || true
cat > /usr/local/bin/nq-backup <<'BAK'
#!/bin/bash
# نسخة احتياطية: قاعدة بيانات السنترال + بيانات اللوحة + إعدادات السنترال → /var/backups/nq (آخر 7 أيام)
# ولو متظبط bucket (sudo nq-setkey backup) → بتترفع على S3 مع التسجيلات
set -o pipefail
set -a; . /etc/nq-calls/env 2>/dev/null; set +a
DAY=$(date +%F); DIR=/var/backups/nq/$DAY; ST=/var/lib/nq-calls/backup.json; ERR=""
mkdir -p "$DIR"; chmod 700 /var/backups/nq
DUMP() { mysqldump --single-transaction --databases asterisk asteriskcdrdb "$@" 2>/tmp/nqb.err | gzip > "$DIR/db.sql.gz"; }
if ! DUMP; then   # لو root مش داخل → بيانات FreePBX
  U=$(sed -n "s/.*AMPDBUSER'\] *= *'\([^']*\)'.*/\1/p" /etc/freepbx.conf 2>/dev/null)
  P=$(sed -n "s/.*AMPDBPASS'\] *= *'\([^']*\)'.*/\1/p" /etc/freepbx.conf 2>/dev/null)
  MYSQL_PWD="$P" DUMP -u"$U" || ERR="قاعدة البيانات: $(head -c 200 /tmp/nqb.err)"
fi
tar czf "$DIR/files.tgz" --exclude=/var/lib/nq-calls/models /var/lib/nq-calls /etc/nq-calls /etc/asterisk 2>/dev/null || [[ $? == 1 ]] || ERR="${ERR:+$ERR · }ملفات"
find /var/backups/nq -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} + 2>/dev/null
S3=""
AWS=$(command -v aws || echo /opt/nq-calls/venv/bin/aws)
if [[ -n "${NQ_BACKUP_BUCKET:-}" ]]; then
  B="${NQ_BACKUP_BUCKET%/}"; [[ "$B" == s3://* ]] || B="s3://$B"; S3="$B"
  "$AWS" s3 cp --recursive --quiet "$DIR" "$B/daily/$DAY/" 2>/tmp/nqb.err || ERR="${ERR:+$ERR · }S3: $(tail -c 200 /tmp/nqb.err)"
  "$AWS" s3 sync --quiet /var/spool/asterisk/monitor "$B/recordings/" 2>>/tmp/nqb.err || ERR="${ERR:+$ERR · }تسجيلات S3"
  [[ -d /var/lib/nq-calls/mobile ]] && "$AWS" s3 sync --quiet /var/lib/nq-calls/mobile "$B/mobile/" 2>>/tmp/nqb.err
fi
# Google Drive (sudo nq-setkey google) — بيرفع على فولدر NQ-Backup في الدرايف
RC=/etc/nq-calls/rclone.conf
if [[ -f "$RC" ]] && grep -q '^\[gdrive\]' "$RC"; then
  G="gdrive:NQ-Backup"; S3="${S3:+$S3 + }Google Drive (NQ-Backup)"
  rclone --config "$RC" copy "$DIR" "$G/daily/$DAY" 2>/tmp/nqb.err || ERR="${ERR:+$ERR · }Google Drive: $(tail -c 200 /tmp/nqb.err)"
  rclone --config "$RC" copy /var/spool/asterisk/monitor "$G/recordings" 2>>/tmp/nqb.err || ERR="${ERR:+$ERR · }تسجيلات Google Drive"
  [[ -d /var/lib/nq-calls/mobile ]] && rclone --config "$RC" copy /var/lib/nq-calls/mobile "$G/mobile" 2>>/tmp/nqb.err
  rclone --config "$RC" delete "$G/daily" --min-age 30d 2>/dev/null; rclone --config "$RC" rmdirs "$G/daily" --leave-root 2>/dev/null
fi
SIZE=$(du -sh "$DIR" | cut -f1)
OK=$([[ -z "$ERR" ]] && echo true || echo false)
ERR="$ERR" python3 -c "import json,os,datetime as d;json.dump({'at':d.datetime.now(d.timezone.utc).isoformat(),'ok':$( [[ $OK == true ]] && echo True || echo False ),'s3':'$S3','size':'$SIZE','error':os.environ['ERR']},open('$ST','w'),ensure_ascii=False)"
chown asterisk:asterisk "$ST" 2>/dev/null
[[ $OK == true ]] && echo "✔ النسخة الاحتياطية اتعملت ($SIZE)${S3:+ واترفعت على $S3}" || { echo "✖ $ERR"; exit 1; }
BAK
chmod 755 /usr/local/bin/nq-backup
echo "0 2 * * * root /usr/local/bin/nq-backup >> /var/log/nq-backup.log 2>&1" > /etc/cron.d/nq-backup
[[ -f "$DATA/backup.json" ]] || /usr/local/bin/nq-backup >/dev/null 2>&1 || true

echo "== listen / whisper / barge (*741 / *742 / *743 + ext)"
EC=/etc/asterisk/extensions_custom.conf; touch "$EC"
sed -i '/;; NQ-SPY-BEGIN/,/;; NQ-SPY-END/d; /include => nq-spy ;nq/d' "$EC"
if grep -q '^\[from-internal-custom\]' "$EC"; then
  sed -i '0,/^\[from-internal-custom\]/s//[from-internal-custom]\ninclude => nq-spy ;nq/' "$EC"
else
  printf '\n[from-internal-custom]\ninclude => nq-spy ;nq\n' >> "$EC"
fi
cat >> "$EC" <<'SPY'
;; NQ-SPY-BEGIN  (متضاف من لوحة NQ — بيتكتب تاني مع كل تحديث)
[nq-spy]
exten => _*741X.,1,Gosub(nq-spy-do,s,1(${EXTEN:4},qE))
exten => _*742X.,1,Gosub(nq-spy-do,s,1(${EXTEN:4},qwE))
exten => _*743X.,1,Gosub(nq-spy-do,s,1(${EXTEN:4},qBE))
[nq-spy-do]
exten => s,1,Set(NQA=${DB(NQSPY/${CALLERID(num)})})
 same => n,GotoIf($["${NQA}" = "*"]?ok)
 same => n,GotoIf($["${REGEX("(^|,)${ARG1}(,|$)" ${NQA})}" = "1"]?ok)
 same => n,Playback(ss-noservice)
 same => n,Hangup()
 same => n(ok),Answer()
 same => n,ChanSpy(PJSIP/${ARG1}-,${ARG2})
 same => n,Hangup()
;; NQ-SPY-END
SPY
chown asterisk:asterisk "$EC" 2>/dev/null
asterisk -rx "dialplan reload" >/dev/null 2>&1 || true

# تغيير مفتاح Claude: sudo nq-setkey
cat > /usr/local/bin/nq-setkey <<'KEY'
#!/bin/bash
# sudo nq-setkey          -> مفتاح Claude (Anthropic) للتحليل
# sudo nq-setkey openai   -> مفتاح OpenAI لتحويل الصوت لنص
# sudo nq-setkey crm      -> توكن الـ CRM لتسجيل المواعيد
# sudo nq-setkey crm-login -> يوزر وباسورد الـ CRM (التوكن يتجدد لوحده)
# sudo nq-setkey telegram -> بوت تليجرام للتنبيهات والملخص اليومي
# sudo nq-setkey backup   -> bucket على S3 للنسخة الاحتياطية
# sudo nq-setkey google   -> النسخة الاحتياطية على Google Drive
# sudo nq-setkey partner  -> مفتاح بوابة الشركاء (فحص الرقم في الـ CRM والسنترال)
# sudo nq-setkey whatsapp -> ربط واتساب الشركة (WhatsApp Cloud API)
set -e
F=/etc/nq-calls/env
setv() { local n=$1 v=$2; grep -v "^$n=" "$F" > "$F.tmp" || true; printf '%s=%s\n' "$n" "$v" >> "$F.tmp"; cat "$F.tmp" > "$F"; rm -f "$F.tmp"; }
if [[ "${1:-}" == crm-login ]]; then
  read -rp "يوزر الـ CRM: " U; read -rsp "باسورد الـ CRM: " P; echo; read -rp "companyDB (لو مش عارفه سيبه فاضي): " D
  [[ -n "$U" && -n "$P" ]] || { echo "✖ لازم يوزر وباسورد"; exit 1; }
  setv NQ_CRM_USER "$U"; setv NQ_CRM_PASS "$P"; setv NQ_CRM_DB "$D"
  BASE=$(python3 -c "import json;from urllib.parse import urlsplit as u;x=u(json.load(open('/var/lib/nq-calls/crm.json')).get('url') or 'https://national-quality-api-site-nq.premiumasp.net/');print(x.scheme+'://'+x.netloc)" 2>/dev/null || echo https://national-quality-api-site-nq.premiumasp.net)
  BODY=$(U="$U" P="$P" D="$D" python3 -c "import json,os;print(json.dumps({'username':os.environ['U'],'password':os.environ['P'],'companyDB':os.environ['D']}))")
  R=$(curl -s -X POST "$BASE/api/v1/authentication/sign-in" -H 'Content-Type: application/json; ver=1.0' -d "$BODY")
  systemctl restart nq-calls
  if printf '%s' "$R" | grep -q '"accessToken" *: *"[^"]'; then echo "✔ الدخول على الـ CRM شغال — التوكن هيتجدد لوحده"; else echo "✖ الـ CRM رفض الدخول: $(printf '%s' "$R" | head -c 300)"; fi
  exit 0
fi
if [[ "${1:-}" == telegram ]]; then
  echo "1) في تليجرام كلّم @BotFather واكتب /newbot وخد التوكن"
  echo "2) اعمل جروب وضيف البوت فيه واكتب في الجروب /start  (أو ابعت /start للبوت نفسه في الخاص)"
  read -rsp "الصق توكن البوت (مش هيظهر وانت بتلصقه): " T; echo
  T=$(printf '%s' "$T" | tr -d '[:space:]')
  OUT=$(TG_T="$T" python3 - <<'PY'
import json, os, sys, urllib.parse, urllib.request
t = os.environ["TG_T"]
def call(m, **kw):
    try:
        r = urllib.request.urlopen(f"https://api.telegram.org/bot{t}/{m}", urllib.parse.urlencode(kw).encode() if kw else None, timeout=15)
        return json.load(r)
    except urllib.error.HTTPError as e:
        try: return json.load(e)
        except Exception: return {"ok": False, "description": str(e)}
    except Exception as e:
        return {"ok": False, "description": str(e)}
me = call("getMe")
if not me.get("ok"):
    print("✖ التوكن غلط أو ناقص — انسخه تاني من BotFather (" + str(me.get("description")) + ")", file=sys.stderr); sys.exit(1)
print("✔ البوت: @" + me["result"]["username"], file=sys.stderr)
chats = {}
for u in call("getUpdates").get("result", []):
    for k in ("message", "my_chat_member", "channel_post", "edited_message"):
        c = (u.get(k) or {}).get("chat")
        if c: chats[c["id"]] = c.get("title") or c.get("first_name") or str(c["id"])
if not chats:
    print("✖ البوت لسه ماوصلوش ولا رسالة — اكتب /start في الجروب (أو في الخاص مع البوت) وشغّل الأمر تاني", file=sys.stderr); sys.exit(1)
cid = list(chats)[-1]
if len(chats) > 1:
    print("لقيت أكتر من شات:", file=sys.stderr)
    for i, (k, v) in enumerate(chats.items(), 1): print(f"  {i}) {v}", file=sys.stderr)
    sys.stderr.write("اختار رقم: "); sys.stderr.flush()
    try: cid = list(chats)[int(open("/dev/tty").readline().strip()) - 1]
    except Exception: pass
r = call("sendMessage", chat_id=cid, text="✅ تنبيهات NQ اتوصلت هنا")
if not r.get("ok") and (r.get("parameters") or {}).get("migrate_to_chat_id"):
    cid = r["parameters"]["migrate_to_chat_id"]; r = call("sendMessage", chat_id=cid, text="✅ تنبيهات NQ اتوصلت هنا")
if not r.get("ok"):
    print("✖ تليجرام رفض الرسالة: " + str(r.get("description")), file=sys.stderr); sys.exit(1)
print(f"✔ وصلت رسالة تجربة على: {chats.get(cid, cid)}", file=sys.stderr)
print(cid)
PY
) || { echo "✖ اتلغى"; exit 1; }
  setv NQ_TG_TOKEN "$T"; setv NQ_TG_CHAT "$OUT"; systemctl restart nq-calls
  echo "✔ التنبيهات هتوصل هنا من دلوقتي"
  exit 0
fi
if [[ "${1:-}" == whatsapp-token ]]; then   # تجديد التوكن بس (الأرقام زي ما هي)
  PID=$(grep -s '^NQ_WA_PHONE_ID=' "$F" | cut -d= -f2-); [[ -n "$PID" ]] || { echo "✖ واتساب مش متربط — استخدم: sudo nq-setkey whatsapp"; exit 1; }
  read -rsp "التوكن الدائم (System User — Never expire): " TOK; echo
  TOK=$(printf '%s' "$TOK" | tr -d '[:space:]'); [[ -n "$TOK" ]] || { echo "✖ اتلغى"; exit 1; }
  R=$(curl -s "https://graph.facebook.com/v21.0/$PID?fields=display_phone_number,verified_name" -H "Authorization: Bearer $TOK")
  echo "$R" | grep -q display_phone_number || { echo "✖ Meta رفض التوكن: $R"; exit 1; }
  setv NQ_WA_TOKEN "$TOK"; systemctl restart nq-calls
  WABA=$(grep -s '^NQ_WA_WABA_ID=' "$F" | cut -d= -f2-)
  [[ -n "$WABA" ]] && curl -s -X POST "https://graph.facebook.com/v21.0/$WABA/subscribed_apps" -H "Authorization: Bearer $TOK" >/dev/null
  echo "✔ التوكن اتجدد — متربط بالرقم: $(echo "$R" | python3 -c 'import json,sys;j=json.load(sys.stdin);print(j.get("display_phone_number"),"—",j.get("verified_name"))')"
  echo "✔ اشتركنا في الرسايل الواردة. Verify token للـ webhook: $(grep -s '^NQ_WA_VERIFY=' "$F" | cut -d= -f2-)"
  exit 0
fi
if [[ "${1:-}" == whatsapp ]]; then
  echo "بيانات WhatsApp Cloud API من developers.facebook.com ← تطبيقك ← WhatsApp ← API Setup:"
  read -rp "Phone number ID: " PID; read -rp "WhatsApp Business Account ID (WABA ID): " WABA
  read -rsp "Access token (الدائم من System User): " TOK; echo
  read -rsp "App secret (App settings ← Basic — أو Enter): " SEC; echo
  TOK=$(printf '%s' "$TOK" | tr -d '[:space:]'); [[ -n "$PID" && -n "$TOK" ]] || { echo "✖ ناقص بيانات"; exit 1; }
  VER=$(grep -s '^NQ_WA_VERIFY=' "$F" | cut -d= -f2-); [[ -n "$VER" ]] || VER=$(python3 -c "import secrets;print(secrets.token_urlsafe(18))")
  setv NQ_WA_PHONE_ID "$PID"; setv NQ_WA_WABA_ID "$WABA"; setv NQ_WA_TOKEN "$TOK"; setv NQ_WA_VERIFY "$VER"
  [[ -n "$SEC" ]] && setv NQ_WA_APP_SECRET "$SEC"
  systemctl restart nq-calls
  R=$(curl -s "https://graph.facebook.com/v21.0/$PID?fields=display_phone_number,verified_name" -H "Authorization: Bearer $TOK")
  echo "$R" | grep -q display_phone_number && echo "✔ متربط بالرقم: $(echo "$R" | python3 -c 'import json,sys;j=json.load(sys.stdin);print(j.get("display_phone_number"),"—",j.get("verified_name"))')" || echo "✖ Meta رفض البيانات: $R"
  echo; echo "دلوقتي في Meta ← WhatsApp ← Configuration ← Webhook ← Edit:"
  H=$(grep -s '^NQ_HOST=' "$F" | cut -d= -f2-); [[ -n "$H" ]] || H="$(curl -s -m 3 http://169.254.169.254/latest/meta-data/public-ipv4 || hostname -I | cut -d' ' -f1).sslip.io"
  echo "   Callback URL:  https://$H/calls-app/wa-hook"
  echo "   Verify token:  $VER"
  echo "   وبعدها اعمل Subscribe على: messages"
  exit 0
fi
if [[ "${1:-}" == email ]]; then
  echo "إرسال التقرير اليومي بالإيميل (SMTP). مثال Gmail: smtp.gmail.com / 587 / إيميلك / App Password (مش باسورد الإيميل):"
  echo "  الـ App Password من: myaccount.google.com ← Security ← 2-Step Verification ← App passwords"
  read -rp "SMTP server [smtp.gmail.com]: " SH; SH=${SH:-smtp.gmail.com}
  read -rp "Port [587]: " SP; SP=${SP:-587}
  read -rp "اليوزر (الإيميل اللي هيبعت): " SU
  read -rsp "الباسورد / App password: " SW; echo
  read -rp "اسم المرسل يظهر كـ [NQ Call Center]: " SN; SN=${SN:-NQ Call Center}
  SW=$(printf '%s' "$SW" | tr -d ' '); [[ -n "$SU" && -n "$SW" ]] || { echo "✖ ناقص بيانات"; exit 1; }
  setv NQ_SMTP_HOST "$SH"; setv NQ_SMTP_PORT "$SP"; setv NQ_SMTP_USER "$SU"; setv NQ_SMTP_PASS "$SW"; setv NQ_SMTP_NAME "$SN"
  systemctl restart nq-calls
  python3 - "$SH" "$SP" "$SU" "$SW" <<'PY' && echo "✔ الإيميل اتربط — حط إيميلات المديرين من اللوحة ودوس 📧 تجربة" || echo "✖ السيرفر رفض الدخول — اتأكد من البيانات (Gmail لازم App Password)"
import smtplib, ssl, sys
h, p, u, w = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
s = smtplib.SMTP_SSL(h, p, timeout=15, context=ssl.create_default_context()) if p == 465 else smtplib.SMTP(h, p, timeout=15)
if p != 465: s.starttls(context=ssl.create_default_context())
s.login(u, w); s.quit()
PY
  exit 0
fi
if [[ "${1:-}" == partner ]]; then
  K=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
  setv NQ_PARTNER_KEY "$K"; systemctl restart nq-calls
  echo "✔ مفتاح بوابة الشركاء اتعمل. انسخ السطر ده وحطه في GitHub (مستودع newCustomer):"
  echo "   Settings → Secrets and variables → Actions → New repository secret"
  echo "   الاسم:  CENTRAL_KEY"
  echo "   القيمة: $K"
  echo "(لو عملت الأمر ده تاني المفتاح القديم هيبطل)"
  exit 0
fi
if [[ "${1:-}" == google ]]; then
  command -v rclone >/dev/null || apt-get install -y -qq rclone >/dev/null
  echo "1) من الموبايل بس (رابط تفتحه وتوافق)"
  echo "2) عندي سطر التوكن من rclone على اللاب"
  read -rp "اختار 1 أو 2 [1]: " WAY
  if [[ "${WAY:-1}" == 2 ]]; then
    read -rp "الصق السطر هنا: " TOK
  else
    pkill -f "rclone authorize" 2>/dev/null || true; LOG=$(mktemp)
    rclone authorize drive --auth-no-open-browser >"$LOG" 2>&1 & RP=$!
    for i in $(seq 1 20); do LOCAL=$(grep -o 'http://127.0.0.1:53682/auth[^ ]*' "$LOG" | head -1); [[ -n "$LOCAL" ]] && break; sleep 0.5; done
    GURL=$( [[ -n "$LOCAL" ]] && curl -s --noproxy '*' -o /dev/null -w '%{redirect_url}' "$LOCAL" )
    [[ -n "$GURL" ]] || { kill $RP 2>/dev/null; echo "✖ rclone مش راضي يبدأ: $(tail -c 300 "$LOG")"; exit 1; }
    echo; echo "① انسخ الرابط ده وافتحه في متصفح الموبايل:"; echo; echo "$GURL"; echo
    echo "② اختار حساب جوجل ودوس Allow / سماح"
    echo "③ هتلاقي صفحة فيها خطأ (ده طبيعي) — انسخ الرابط اللي فوق في شريط العنوان كله (بيبدأ بـ http://127.0.0.1:53682)"
    read -rp "الصق الرابط هنا: " BACK
    [[ "$BACK" == http://127.0.0.1:53682/* ]] || { kill $RP 2>/dev/null; echo "✖ الرابط لازم يبدأ بـ http://127.0.0.1:53682"; exit 1; }
    curl -s --noproxy '*' -o /dev/null "$BACK" || true
    for i in $(seq 1 30); do kill -0 $RP 2>/dev/null || break; sleep 0.5; done; kill $RP 2>/dev/null || true
    TOK=$(tr -d '\n' < "$LOG" | grep -o '{"access_token"[^}]*}' | tail -1 || true); rm -f "$LOG"
    [[ -n "$TOK" ]] || { echo "✖ جوجل مارجّعش التوكن — جرّب تاني من الأول (الرابط بيخلص بسرعة)"; exit 1; }
  fi
  TOK=$(printf '%s' "$TOK" | sed 's/^.*\({.*}\).*$/\1/')
  [[ "$TOK" == "{"*"}" ]] || { echo "✖ ده مش التوكن — لازم يبدأ بـ { ويخلص بـ }"; exit 1; }
  RC=/etc/nq-calls/rclone.conf; rm -f "$RC"
  printf '[gdrive]\ntype = drive\nscope = drive\ntoken = %s\n' "$TOK" > "$RC"; chmod 600 "$RC"
  rclone --config "$RC" mkdir gdrive:NQ-Backup && echo "✔ اتربط بجوجل درايف — الفولدر NQ-Backup" || { echo "✖ مش قادر يكتب في الدرايف"; exit 1; }
  echo "بنجرّب نسخة دلوقتي (أول مرة بتاخد وقت عشان التسجيلات)..."; /usr/local/bin/nq-backup; exit $?
fi
if [[ "${1:-}" == backup ]]; then
  echo "اعمل bucket في AWS S3 (مثلاً nq-backup-2026) — ولو السيرفر عليه IAM role فيها صلاحية S3 سيب المفاتيح فاضية"
  read -rp "اسم الـ bucket: " B; [[ -n "$B" ]] || exit 1
  read -rp "AWS Access Key ID (أو Enter): " AK
  if [[ -n "$AK" ]]; then read -rsp "AWS Secret Access Key: " SK; echo; setv AWS_ACCESS_KEY_ID "$AK"; setv AWS_SECRET_ACCESS_KEY "$SK"; fi
  RG=$(curl -s -m 3 http://169.254.169.254/latest/meta-data/placement/region || true); setv AWS_DEFAULT_REGION "${RG:-us-east-1}"
  setv NQ_BACKUP_BUCKET "$B"; systemctl restart nq-calls
  echo "بنجرّب نسخة دلوقتي..."; /usr/local/bin/nq-backup; exit $?
fi
if [[ "${1:-}" == crm ]]; then
  NAME=NQ_CRM_TOKEN; PFX=""; LBL="الـ CRM"
elif [[ "${1:-}" == openai ]]; then
  NAME=OPENAI_API_KEY; PFX=sk-; LBL="OpenAI"
else
  NAME=ANTHROPIC_API_KEY; PFX=sk-ant-; LBL="Anthropic"
fi
read -rsp "الصق مفتاح $LBL الجديد: " K; echo
K=$(printf '%s' "$K" | tr -d '[:space:]')
[[ "$K" == $PFX* ]] || { echo "✖ المفتاح لازم يبدأ بـ $PFX"; exit 1; }
if grep -q "^$NAME=" "$F"; then sed -i "s|^$NAME=.*|$NAME=$K|" "$F"; else echo "$NAME=$K" >> "$F"; fi
systemctl restart nq-calls
if [[ $NAME == NQ_CRM_TOKEN ]]; then
  echo "✔ توكن الـ CRM اتحفظ — جرّب تأكيد موعد من اللوحة"; exit 0
fi
if [[ $NAME == OPENAI_API_KEY ]]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' https://api.openai.com/v1/models -H "Authorization: Bearer $K")
else
  CODE=$(curl -s -o /dev/null -w '%{http_code}' https://api.anthropic.com/v1/models -H "x-api-key: $K" -H "anthropic-version: 2023-06-01")
fi
[[ "$CODE" == 200 ]] && echo "✔ مفتاح $LBL شغال واتحفظ" || echo "✖ $LBL رفض المفتاح (كود $CODE) — اتأكد إنك نسخته كامل وإن الحساب عليه رصيد"
KEY
chmod 755 /usr/local/bin/nq-setkey

echo "== check"
# اللوحة محتاجة تسجيل دخول دلوقتي — بنتأكد إن السيرفر شغال من الصفحة الرئيسية (مش محمية)
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS -o /dev/null http://127.0.0.1:8090/; then break; fi; sleep 2
done
if curl -fsS -o /dev/null http://127.0.0.1:8090/ && [[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/api/config)" == 401 ]]; then
  echo "service OK (اللوحة محمية بتسجيل دخول ✔)"
else
  echo "✖ السيرفر مش بيرد — journalctl -u nq-calls -n 30"
fi
journalctl -u nq-calls -n 8 --no-pager 2>/dev/null | grep -iE "error|exception" | tail -3 || true
echo "DONE  ->  https://$HOST/calls/   (user: admin)"
