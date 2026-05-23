import os
import requests
import urllib.request
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

NAMECOM_USERNAME = os.environ["NAMECOM_USERNAME"]
NAMECOM_API_TOKEN = os.environ["NAMECOM_API_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

# ── name.com API ─────────────────────────────────────────────────────────────
def get_namecom_domains():
    domains = []
    page = 1
    while True:
        resp = requests.get(
            "https://api.name.com/v4/domains",
            params={"page": page, "perPage": 1000},
            auth=(NAMECOM_USERNAME, NAMECOM_API_TOKEN),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("domains", [])
        domains.extend(batch)
        if len(batch) < 1000:
            break
        page += 1
    return domains

# ── Telegram ─────────────────────────────────────────────────────────────────
def send_message(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }, timeout=15)

# ── Domain Check ─────────────────────────────────────────────────────────────
def check_domains(chat_id, limit=5):
    send_message(chat_id, "🔍 กำลังตรวจสอบโดเมน...")
    try:
        domains = get_namecom_domains()
    except Exception as e:
        send_message(chat_id, f"❌ เชื่อมต่อ name.com ไม่ได้: {e}")
        return

    now = datetime.now(timezone.utc)
    domain_list = []

    for domain in domains:
        name = domain.get("domainName", "")
        expire_str = domain.get("expireDate", "")
        auto_renew = domain.get("autorenewEnabled", False)
        if not expire_str:
            continue
        try:
            expire_dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        days_left = (expire_dt - now).days
        domain_list.append((days_left, name, expire_dt, auto_renew, "name.com"))

    domain_list.sort(key=lambda x: x[0])

    if not domain_list:
        send_message(chat_id, "ℹ️ ไม่พบโดเมนในบัญชีของคุณ")
        return

    top = domain_list[:limit]
    now_str = now.strftime("%d %b %Y %H:%M")
    lines = [
        f"📋 <b>โดเมนใกล้หมดอายุที่สุด {limit} อันดับ</b>\n"
        f"🕐 ตรวจสอบเมื่อ: {now_str} UTC\n"
        f"{'─' * 30}\n"
    ]

    for days_left, name, expire_dt, auto_renew, source in top:
        if days_left <= 0:
            status = "🔴 <b>หมดอายุแล้ว!</b>"
        elif days_left <= 7:
            status = f"🔴 <b>เหลือ {days_left} วัน</b>"
        elif days_left <= 14:
            status = f"🟠 เหลือ {days_left} วัน"
        elif days_left <= 30:
            status = f"🟡 เหลือ {days_left} วัน"
        else:
            status = f"🟢 เหลือ {days_left} วัน"

        renew_note = "✅ Auto-renew เปิด" if auto_renew else "❌ Auto-renew ปิด"
        expire_date = expire_dt.strftime("%d %b %Y")

        lines.append(
            f"🌐 <b>{name}</b> <i>({source})</i>\n"
            f"   {status}\n"
            f"   📅 หมดอายุ: {expire_date}\n"
            f"   {renew_note}"
        )

    send_message(chat_id, "\n\n".join(lines))

# ── Webhook ───────────────────────────────────────────────────────────────────
@app.route("/webhook/<token>", methods=["POST"])
def webhook(token):
    data = request.json
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "").strip().lower()

    if not chat_id:
        return jsonify(ok=True)

    if "/check10" in text:
        check_domains(chat_id, limit=10)
    elif "/check" in text:
        check_domains(chat_id)
    elif "/start" in text or "/help" in text:
        send_message(chat_id,
            "👋 <b>Domain Alert Bot</b>\n\n"
            "คำสั่งที่ใช้ได้:\n"
            "🔍 /check — ดูโดเมนใกล้หมดอายุที่สุด 5 อันดับ\n"
            "📋 /check10 — ดู 10 อันดับ\n"
            "ℹ️ /help — แสดงคำสั่งทั้งหมด"
        )

    return jsonify(ok=True)

@app.route("/", methods=["GET"])
def index():
    return "Domain Alert Bot is running! 🚀"

@app.route("/ip", methods=["GET"])
def get_ip():
    ip = urllib.request.urlopen("https://api.ipify.org").read().decode()
    return ip

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
