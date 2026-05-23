import os
import requests
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

NAMECOM_USERNAME = os.environ["NAMECOM_USERNAME"]
NAMECOM_API_TOKEN = os.environ["NAMECOM_API_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

NAMECHEAP_ACCOUNTS = {}
for i in range(1, 4):
    u = os.environ.get(f"NAMECHEAP_USERNAME_{i}", "")
    k = os.environ.get(f"NAMECHEAP_API_KEY_{i}", "")
    l = os.environ.get(f"NAMECHEAP_LABEL_{i}", f"ไอดีที่ {i}")
    if u and k:
        NAMECHEAP_ACCOUNTS[i] = {"username": u, "api_key": k, "label": l}

# ── name.com API ──────────────────────────────────────────────────────────────
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
        for d in batch:
            expire_str = d.get("expireDate", "")
            if not expire_str:
                continue
            try:
                expire_dt = datetime.fromisoformat(expire_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            domains.append({
                "name": d.get("domainName", ""),
                "expire_dt": expire_dt,
                "auto_renew": d.get("autorenewEnabled", False),
                "source": "name.com",
            })
        if len(batch) < 1000:
            break
        page += 1
    return domains

# ── Namecheap API ─────────────────────────────────────────────────────────────
def get_namecheap_domains(account_num):
    acc = NAMECHEAP_ACCOUNTS.get(account_num)
    if not acc:
        return [], f"❌ ไม่พบข้อมูล Namecheap ไอดีที่ {account_num}"
    try:
        client_ip = urllib.request.urlopen("https://api.ipify.org", timeout=10).read().decode()
        resp = requests.get(
            "https://api.namecheap.com/xml.response",
            params={
                "ApiUser": acc["username"],
                "ApiKey": acc["api_key"],
                "UserName": acc["username"],
                "Command": "namecheap.domains.getList",
                "ClientIp": client_ip,
                "PageSize": 100,
            },
            timeout=30,
        )
        root = ET.fromstring(resp.text)
        ns = {"nc": "http://api.namecheap.com/xml.response"}

        status = root.get("Status", "")
        if status == "ERROR":
            errors = root.findall(".//nc:Error", ns)
            msg = errors[0].text if errors else "Unknown error"
            return [], f"❌ Namecheap [{acc['label']}]: {msg}"

        domains = []
        for d in root.findall(".//nc:Domain", ns):
            name = d.get("Name", "")
            expires = d.get("Expires", "")
            auto_renew = d.get("AutoRenew", "false").lower() == "true"
            if not expires:
                continue
            try:
                expire_dt = datetime.strptime(expires, "%m/%d/%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            domains.append({
                "name": name,
                "expire_dt": expire_dt,
                "auto_renew": auto_renew,
                "source": f"Namecheap [{acc['label']}]",
            })
        return domains, None
    except Exception as e:
        return [], f"❌ Namecheap [{acc['label']}]: {e}"

# ── Telegram ──────────────────────────────────────────────────────────────────
def send_message(chat_id, message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }, timeout=15)

# ── Format & Send Domain List ─────────────────────────────────────────────────
def send_domain_list(chat_id, domains, title, limit=5):
    if not domains:
        send_message(chat_id, "ℹ️ ไม่พบโดเมนในบัญชีนี้")
        return

    now = datetime.now(timezone.utc)
    for d in domains:
        d["days_left"] = (d["expire_dt"] - now).days

    domains.sort(key=lambda x: x["days_left"])
    top = domains[:limit]

    now_str = now.strftime("%d %b %Y %H:%M")
    lines = [
        f"📋 <b>{title}</b>\n"
        f"🕐 ตรวจสอบเมื่อ: {now_str} UTC\n"
        f"📊 รวมทั้งหมด {len(domains)} โดเมน\n"
        f"{'─' * 30}\n"
    ]

    for d in top:
        days_left = d["days_left"]
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

        renew_note = "✅ Auto-renew เปิด" if d["auto_renew"] else "❌ Auto-renew ปิด"
        expire_date = d["expire_dt"].strftime("%d %b %Y")

        lines.append(
            f"🌐 <b>{d['name']}</b> <i>({d['source']})</i>\n"
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

    if "/checknc1" in text:
        acc = NAMECHEAP_ACCOUNTS.get(1, {})
        label = acc.get("label", "ไอดีที่ 1")
        send_message(chat_id, f"🔍 กำลังตรวจสอบ Namecheap [{label}]...")
        domains, err = get_namecheap_domains(1)
        if err:
            send_message(chat_id, err)
        else:
            send_domain_list(chat_id, domains, f"Namecheap ไอดีที่ 1 [{label}]")

    elif "/checknc2" in text:
        acc = NAMECHEAP_ACCOUNTS.get(2, {})
        label = acc.get("label", "ไอดีที่ 2")
        send_message(chat_id, f"🔍 กำลังตรวจสอบ Namecheap [{label}]...")
        domains, err = get_namecheap_domains(2)
        if err:
            send_message(chat_id, err)
        else:
            send_domain_list(chat_id, domains, f"Namecheap ไอดีที่ 2 [{label}]")

    elif "/checknc3" in text:
        acc = NAMECHEAP_ACCOUNTS.get(3, {})
        label = acc.get("label", "ไอดีที่ 3")
        send_message(chat_id, f"🔍 กำลังตรวจสอบ Namecheap [{label}]...")
        domains, err = get_namecheap_domains(3)
        if err:
            send_message(chat_id, err)
        else:
            send_domain_list(chat_id, domains, f"Namecheap ไอดีที่ 3 [{label}]")

    elif "/check10" in text:
        send_message(chat_id, "🔍 กำลังตรวจสอบโดเมนทั้งหมด...")
        all_domains = []
        try:
            all_domains += get_namecom_domains()
        except Exception as e:
            send_message(chat_id, f"⚠️ name.com: {e}")
        for i in NAMECHEAP_ACCOUNTS:
            d, err = get_namecheap_domains(i)
            if err:
                send_message(chat_id, err)
            else:
                all_domains += d
        send_domain_list(chat_id, all_domains, "โดเมนทั้งหมด", limit=10)

    elif "/check" in text:
        send_message(chat_id, "🔍 กำลังตรวจสอบโดเมนทั้งหมด...")
        all_domains = []
        try:
            all_domains += get_namecom_domains()
        except Exception as e:
            send_message(chat_id, f"⚠️ name.com: {e}")
        for i in NAMECHEAP_ACCOUNTS:
            d, err = get_namecheap_domains(i)
            if err:
                send_message(chat_id, err)
            else:
                all_domains += d
        send_domain_list(chat_id, all_domains, "โดเมนทั้งหมด")

    elif "/start" in text or "/help" in text:
        nc_cmds = ""
        for i, acc in NAMECHEAP_ACCOUNTS.items():
            nc_cmds += f"🔍 /checknc{i} — เช็ค Namecheap [{acc['label']}]\n"
        send_message(chat_id,
            "👋 <b>Domain Alert Bot</b>\n\n"
            "คำสั่งที่ใช้ได้:\n"
            "🔍 /check — ดูโดเมนใกล้หมดอายุที่สุด 5 อันดับ (ทุกไอดี)\n"
            "📋 /check10 — ดู 10 อันดับ (ทุกไอดี)\n"
            f"{nc_cmds}"
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
