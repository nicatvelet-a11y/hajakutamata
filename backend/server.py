"""
server.py - Hapsetme Sorgu Backend v2
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from curl_cffi import requests as cf_requests
import json, os, base64, secrets, string, time, threading, asyncio, re
from datetime import datetime
import discord
from discord.ext import commands, tasks

app = Flask(__name__)

# Max request body: 1MB - buyuk payload DoS/spam engelle
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB
# Yakalanmamis istisnalarin Werkzeug tarafindan istemciye sizmesini engelle
app.config["PROPAGATE_EXCEPTIONS"] = False

# Sadece kendi sitelerinden gelen isteklere izin ver
_ALLOWED_ORIGINS = [
    "https://bedavasorgupanel.netlify.app",
    "https://arastir-01.site",
    "https://www.arastir-01.site",
    "https://arastir.vip",
    "https://www.arastir.vip",
]
CORS(app, origins=_ALLOWED_ORIGINS, supports_credentials=False)

# Flask-CORS bazen OPTIONS pre-flight'ta header dondurmeyebilir - manuel garanti
@app.after_request
def _add_cors_headers(response):
    origin = request.headers.get("Origin", "")
    if origin in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"]       = "600"
    return response

@app.route("/<path:_>", methods=["OPTIONS"])
@app.route("/", methods=["OPTIONS"])
def _options_handler(_=None):
    """Tum endpoint'ler icin OPTIONS pre-flight yaniti."""
    response = app.make_default_options_response()
    origin = request.headers.get("Origin", "")
    if origin in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"]  = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"]       = "600"
    return response

_session = cf_requests.Session(impersonate="chrome124")

# -- Bot Token --
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable eksik!")

# ============================================================
# REDIS (Upstash) - kalici storage
# ============================================================
_REDIS_URL   = os.environ.get("UPSTASH_REDIS_REST_URL",   "")
_REDIS_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")
_redis_lock  = threading.Lock()

if not _REDIS_URL or not _REDIS_TOKEN:
    print("=" * 60)
    print("UYARI: UPSTASH_REDIS_REST_URL veya UPSTASH_REDIS_REST_TOKEN")
    print("       ayarlanmamis - kalici storage devre disi!")
    print("       Veriler yalnizca bellekte tutulacak.")
    print("=" * 60, flush=True)

def _redis_set(key: str, value) -> bool:
    """Redis'e JSON olarak yaz - Upstash REST /set/key/value formati."""
    try:
        import urllib.parse
        data = json.dumps(value, ensure_ascii=False)
        encoded = urllib.parse.quote(data, safe='')
        r = _session.get(
            f"{_REDIS_URL}/set/{key}/{encoded}",
            headers={"Authorization": f"Bearer {_REDIS_TOKEN}"},
            timeout=5,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"Redis SET hata ({key}): {e}")
        return False

def _redis_get(key: str):
    """Redis'ten JSON olarak oku. Bulunamazsa None."""
    try:
        r = _session.get(
            f"{_REDIS_URL}/get/{key}",
            headers={"Authorization": f"Bearer {_REDIS_TOKEN}"},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        result = r.json().get("result")
        if result is None:
            return None
        return json.loads(result)
    except Exception as e:
        print(f"Redis GET hata ({key}): {e}")
        return None

if _REDIS_URL:
    print(f"Redis URL: {_REDIS_URL[:40]}...")

# -- Sabitler --
WEBHOOK_LOG          = os.environ.get("WEBHOOK_LOG",    "")
WEBHOOK_TICKET       = os.environ.get("WEBHOOK_TICKET", "")
REPORT_CHANNEL_ID    = 1553958488147759194
ACTIVE_CHANNEL_ID    = 1553958739093094480
KEY_LOG_CHANNEL_ID   = 1554084280983093299
ADMIN_ID             = 330883096826347532
AFK_CHANNEL_ID       = 1554819174210084924
AFK_GUILD_ID         = None  # on_ready'de doldurulur, ileride guild-based kontrol icin
CREDIT_SERVER_INVITE = "https://discord.gg/Q66Vhe3GpP"
KEY_EXPIRE_SECS      = 20 * 60
INITIAL_CREDITS      = 10
AFK_CREDIT_GAIN      = 2
AFK_INTERVAL_SECS    = 300

# -- Kalici Storage --
def _get_storage_dir():
    for d in ["/data", "/app/data", "/app"]:
        try:
            os.makedirs(d, exist_ok=True)
            test = os.path.join(d, ".write_test")
            with open(test, "w") as f:
                f.write("")
            os.remove(test)
            return d
        except Exception:
            continue
    return "/tmp"

_STORAGE_DIR         = _get_storage_dir()
_CREDITS_FILE        = os.path.join(_STORAGE_DIR, "hs_credits.json")
_CREDIT_RESET_FILE   = os.path.join(_STORAGE_DIR, "hs_credit_reset.json")
_KEY_FILE            = os.path.join(_STORAGE_DIR, "hs_keys.json")
_BLACKLIST_FILE      = os.path.join(_STORAGE_DIR, "hs_blacklist.json")
_ANN_FILE            = os.path.join(_STORAGE_DIR, "hs_announcement.json")
_YT_FILE             = os.path.join(_STORAGE_DIR, "hs_youtube.json")
_SETTINGS_FILE       = os.path.join(_STORAGE_DIR, "hs_settings.json")
_MAINTENANCE_FILE    = os.path.join(_STORAGE_DIR, "hs_maintenance.json")
_STATS_FILE          = os.path.join(_STORAGE_DIR, "hs_stats.json")

print(f"Storage dizini: {_STORAGE_DIR}")

# ============================================================
# KREDI STORE
# ============================================================
_credits: dict = {}
_credits_lock = threading.Lock()
_credit_reset_times: dict = {}
_reset_lock = threading.Lock()

def _load_credits():
    global _credits
    # Once Redis'ten yukle
    rdata = _redis_get("credits")
    if rdata is not None and isinstance(rdata, dict):
        with _credits_lock:
            # 0 dahil tum degerler kaydedilsin
            _credits = {str(k): int(v) for k, v in rdata.items()}
        print(f"Kredi store (Redis): {len(_credits)} kullanici")
        return
    # Redis'te yok - dosyadan yukle
    try:
        with open(_CREDITS_FILE, "r") as f:
            data = json.load(f)
        with _credits_lock:
            _credits = {str(k): int(v) for k, v in data.items()}
        print(f"Kredi store (dosya): {len(_credits)} kullanici")
        # Dosyadan yuklendiyse Redis'e de yaz
        _save_credits()
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Kredi yukleme hata: {e}")

def _save_credits():
    try:
        with _credits_lock:
            # 0 dahil TUM degerler kaydedilsin
            data = {str(k): int(v) for k, v in _credits.items()}
        with open(_CREDITS_FILE, "w") as f:
            json.dump(data, f)
        # Redis'e de yaz - deploy'da kaybolmasin
        _redis_set("credits", data)
    except Exception as e:
        print(f"Kredi kayit hata: {e}")

def _get_credits(did: str) -> int:
    with _credits_lock:
        return _credits.get(str(did), -1)

def _set_credits(did: str, amount: int):
    with _credits_lock:
        _credits[str(did)] = max(0, amount)
    _save_credits()

def _add_credits(did: str, amount: int) -> int:
    with _credits_lock:
        cur = _credits.get(str(did), 0)
        _credits[str(did)] = cur + amount
        new = _credits[str(did)]
    _save_credits()
    return new

def _spend_credit(did: str) -> bool:
    with _credits_lock:
        cur = _credits.get(str(did), 0)
        if cur <= 0:
            return False
        _credits[str(did)] = cur - 1
    _save_credits()
    return True

def _load_credit_reset():
    global _credit_reset_times
    rdata = _redis_get("credit_reset")
    if rdata is not None and isinstance(rdata, dict):
        with _reset_lock:
            _credit_reset_times = {str(k): float(v) for k, v in rdata.items()}
        print(f"Credit reset (Redis): {len(_credit_reset_times)} kayit")
        return
    try:
        with open(_CREDIT_RESET_FILE, "r") as f:
            data = json.load(f)
        with _reset_lock:
            _credit_reset_times = {str(k): float(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Credit reset yukleme hata: {e}")

def _save_credit_reset():
    try:
        with _reset_lock:
            data = dict(_credit_reset_times)
        with open(_CREDIT_RESET_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("credit_reset", data)
    except Exception as e:
        print(f"Credit reset kayit hata: {e}")

def _check_and_reset_credit(did: str) -> int:
    """24 saat gectiyse ve kredi 0 ise 10 kredi ver. Kac kredi eklendigini dondurur."""
    now = time.time()
    with _reset_lock:
        last = _credit_reset_times.get(str(did), 0)
    if now - last >= 86400:
        cur = _get_credits(str(did))
        if cur <= 0:
            _set_credits(str(did), INITIAL_CREDITS)
            with _reset_lock:
                _credit_reset_times[str(did)] = now
            _save_credit_reset()
            return INITIAL_CREDITS
        else:
            with _reset_lock:
                _credit_reset_times[str(did)] = now
            _save_credit_reset()
    return 0

# ============================================================
# KEY STORE
# ============================================================
_key_store: dict = {}
_key_lock = threading.Lock()

def _save_keys():
    try:
        with _key_lock:
            data = dict(_key_store)
        with open(_KEY_FILE, "w") as f:
            json.dump(data, f)
        # Redis'e de yaz - deploy sonrasi key kaybolmasin
        _redis_set("keys", data)
    except Exception as e:
        print(f"Key kayit hata: {e}")

def _load_keys():
    global _key_store
    # Once Redis'ten yukle
    rdata = _redis_get("keys")
    if rdata is not None and isinstance(rdata, dict):
        now = time.time()
        valid = {k: v for k, v in rdata.items() if isinstance(v, dict) and v.get("expires", 0) > now}
        with _key_lock:
            _key_store.update(valid)
        print(f"Key store (Redis): {len(valid)} aktif")
        return
    # Dosyadan yukle
    try:
        with open(_KEY_FILE, "r") as f:
            data = json.load(f)
        now = time.time()
        valid = {k: v for k, v in data.items() if v.get("expires", 0) > now}
        with _key_lock:
            _key_store.update(valid)
        print(f"Key store (dosya): {len(valid)} aktif")
        if valid:
            _save_keys()  # Redis'e de yaz
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Key yukleme hata: {e}")

def _generate_key() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(4)) + "-" + "".join(secrets.choice(chars) for _ in range(4))

def _cleanup_expired():
    now = time.time()
    with _key_lock:
        exp = [k for k, v in _key_store.items() if isinstance(v, dict) and v.get("expires", 0) < now]
        for k in exp:
            del _key_store[k]

# ============================================================
# KARA LISTE
# ============================================================
_blacklist: set = set()
_bl_lock = threading.Lock()

def _load_blacklist():
    global _blacklist
    rdata = _redis_get("blacklist")
    if rdata is not None and isinstance(rdata, list):
        with _bl_lock:
            _blacklist = set(str(x) for x in rdata)
        print(f"Kara liste (Redis): {len(_blacklist)} ID")
        return
    try:
        with open(_BLACKLIST_FILE, "r") as f:
            data = json.load(f)
        with _bl_lock:
            _blacklist = set(str(x) for x in data)
        print(f"Kara liste (dosya): {len(_blacklist)} ID")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Kara liste yukleme hata: {e}")

def _save_blacklist():
    try:
        with _bl_lock:
            data = list(_blacklist)
        with open(_BLACKLIST_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("blacklist", data)
    except Exception as e:
        print(f"Kara liste kayit hata: {e}")

def _is_banned(did: str) -> bool:
    with _bl_lock:
        return did in _blacklist

# ============================================================
# DUYURU
# ============================================================
_announcement = {"text": "", "active": False}
_ann_lock = threading.Lock()

def _load_announcement():
    global _announcement
    rdata = _redis_get("announcement")
    if rdata is not None and isinstance(rdata, dict):
        with _ann_lock:
            _announcement["active"] = bool(rdata.get("active", False))
            _announcement["text"]   = str(rdata.get("text", ""))
        print(f"Duyuru (Redis): active={_announcement['active']}")
        return
    try:
        with open(_ANN_FILE, "r") as f:
            data = json.load(f)
        with _ann_lock:
            _announcement["active"] = bool(data.get("active", False))
            _announcement["text"]   = str(data.get("text", ""))
        print(f"Duyuru (dosya): active={_announcement['active']}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Duyuru yukleme hata: {e}")

def _save_announcement():
    try:
        with _ann_lock:
            data = dict(_announcement)
        with open(_ANN_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("announcement", data)
    except Exception as e:
        print(f"Duyuru kayit hata: {e}")

# ============================================================
# YOUTUBE CONFIG
# ============================================================
_yt_config = {"url": "", "id": ""}
_yt_lock   = threading.Lock()

def _load_yt():
    global _yt_config
    rdata = _redis_get("yt_config")
    if rdata is not None and isinstance(rdata, dict):
        with _yt_lock:
            _yt_config["url"] = str(rdata.get("url", ""))
            _yt_config["id"]  = str(rdata.get("id",  ""))
        print(f"YT config (Redis): {_yt_config['url'][:50]}")
        return
    try:
        with open(_YT_FILE, "r") as f:
            data = json.load(f)
        with _yt_lock:
            _yt_config["url"] = str(data.get("url", ""))
            _yt_config["id"]  = str(data.get("id",  ""))
        print(f"YT config (dosya): {_yt_config['url'][:50]}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"YT yukleme hata: {e}")

def _save_yt():
    try:
        with _yt_lock:
            data = dict(_yt_config)
        with open(_YT_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("yt_config", data)
    except Exception as e:
        print(f"YT kayit hata: {e}")

def _extract_yt_id(url: str) -> str:
    patterns = [
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return ""

# ============================================================
# AYARLAR
# ============================================================
_settings = {"key_expire_secs": 20 * 60, "yt_wait_secs": 5}
_settings_lock = threading.Lock()

def _load_settings():
    global _settings, KEY_EXPIRE_SECS
    rdata = _redis_get("settings")
    if rdata is not None and isinstance(rdata, dict):
        with _settings_lock:
            _settings["key_expire_secs"] = int(rdata.get("key_expire_secs", 20 * 60))
            _settings["yt_wait_secs"]    = int(rdata.get("yt_wait_secs",    5))
        KEY_EXPIRE_SECS = _settings["key_expire_secs"]
        print(f"Settings (Redis): key={_settings['key_expire_secs']//60}dk")
        return
    try:
        with open(_SETTINGS_FILE, "r") as f:
            data = json.load(f)
        with _settings_lock:
            _settings["key_expire_secs"] = int(data.get("key_expire_secs", 20 * 60))
            _settings["yt_wait_secs"]    = int(data.get("yt_wait_secs",    5))
        KEY_EXPIRE_SECS = _settings["key_expire_secs"]
        print(f"Settings (dosya): key={_settings['key_expire_secs']//60}dk")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Settings yukleme hata: {e}")

def _save_settings():
    try:
        with _settings_lock:
            data = dict(_settings)
        with open(_SETTINGS_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("settings", data)
    except Exception as e:
        print(f"Settings kayit hata: {e}")

# ============================================================
# BAKIM MODU
# ============================================================
_maintenance = {"active": False, "message": "Site su an bakimda. Lutfen daha sonra tekrar deneyin."}
_maint_lock  = threading.Lock()

def _load_maintenance():
    global _maintenance
    rdata = _redis_get("maintenance")
    if rdata is not None and isinstance(rdata, dict):
        with _maint_lock:
            _maintenance["active"]  = bool(rdata.get("active", False))
            _maintenance["message"] = str(rdata.get("message", _maintenance["message"]))
        print(f"Bakim modu (Redis): active={_maintenance['active']}")
        return
    try:
        with open(_MAINTENANCE_FILE, "r") as f:
            data = json.load(f)
        with _maint_lock:
            _maintenance["active"]  = bool(data.get("active", False))
            _maintenance["message"] = str(data.get("message", _maintenance["message"]))
        print(f"Bakim modu (dosya): active={_maintenance['active']}")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Bakim modu yukleme hata: {e}")

def _save_maintenance():
    try:
        with _maint_lock:
            data = dict(_maintenance)
        with open(_MAINTENANCE_FILE, "w") as f:
            json.dump(data, f)
        _redis_set("maintenance", data)
    except Exception as e:
        print(f"Bakim modu kayit hata: {e}")

# ============================================================
# ISTATISTIKLER
# ============================================================
_stats = {"keys_today": 0, "unique_today": [], "day": datetime.now().strftime("%Y-%m-%d")}
_stats_lock = threading.Lock()

def _load_stats():
    global _stats
    try:
        with open(_STATS_FILE, "r") as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("day") == today:
            with _stats_lock:
                _stats["keys_today"]   = int(data.get("keys_today", 0))
                _stats["unique_today"] = list(data.get("unique_today", []))
                _stats["day"]          = today
        print(f"Stats: {_stats['keys_today']} key bugun")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Stats yukleme hata: {e}")

def _save_stats():
    try:
        with _stats_lock:
            data = {
                "keys_today":   _stats["keys_today"],
                "unique_today": list(_stats["unique_today"]),
                "day":          _stats["day"],
            }
        with open(_STATS_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Stats kayit hata: {e}")

def _reset_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    with _stats_lock:
        if _stats["day"] != today:
            _stats["keys_today"]   = 0
            _stats["unique_today"] = []
            _stats["day"]          = today

# ============================================================
# DISCORD BOT
# ============================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="/", intents=intents)

def _is_admin(ctx) -> bool:
    return isinstance(ctx.channel, discord.DMChannel) and ctx.author.id == ADMIN_ID

# ============================================================
# BOT YARDIMCI FONKSIYONLARI
# ============================================================
async def send_dm_key(discord_id: int, username: str, key: str) -> bool:
    try:
        user = await bot.fetch_user(discord_id)
        embed = discord.Embed(title="Hapsetme Sorgu - Erisim Anahtariniz", color=0x6366f1)
        embed.add_field(name="Anahtar", value=f"```{key}```", inline=False)
        embed.add_field(name="Sure",    value="20 dakika gecerlidir", inline=True)
        embed.add_field(name="Uyari",   value="Her cikista yeni anahtar uretilir", inline=False)
        embed.set_footer(text="Hapsetme Sorgu - Bu anahtari kimseyle paylasmayiniz")
        await user.send(embed=embed)
        return True
    except Exception as e:
        print(f"DM hatasi: {e}")
        return False

async def log_webhook(discord_id: int, username: str, key: str):
    if not WEBHOOK_LOG:
        return
    try:
        payload = {"embeds": [{"title": "Yeni Key Uretildi", "color": 0x57f287, "fields": [
            {"name": "Discord", "value": username,        "inline": True},
            {"name": "ID",      "value": str(discord_id), "inline": True},
            {"name": "Key",     "value": key,             "inline": True},
            {"name": "Zaman",   "value": time.strftime("%d.%m.%Y %H:%M:%S"), "inline": True},
        ], "footer": {"text": "Hapsetme Key Logger"}}]}
        _session.post(WEBHOOK_LOG, json=payload, timeout=8)
    except Exception as e:
        print(f"Webhook hata: {e}")

async def log_key_channel(discord_id: int, username: str, key: str):
    try:
        ch = bot.get_channel(KEY_LOG_CHANNEL_ID)
        if not ch:
            ch = await bot.fetch_channel(KEY_LOG_CHANNEL_ID)
        if not ch:
            print(f"log_key_channel: kanal bulunamadi ID={KEY_LOG_CHANNEL_ID}")
            return
        embed = discord.Embed(title="Yeni Key Uretildi", color=0x6366f1, timestamp=discord.utils.utcnow())
        embed.add_field(name="Discord Adi", value=f"**{username}**",   inline=True)
        embed.add_field(name="Discord ID",  value=f"`{discord_id}`",   inline=True)
        embed.add_field(name="Key",         value=f"```{key}```",      inline=False)
        embed.add_field(name="Sure",        value="20 dakika",         inline=True)
        embed.add_field(name="Zaman",       value=datetime.now().strftime("%d.%m.%Y %H:%M:%S"), inline=True)
        embed.set_footer(text="Hapsetme Key Log")
        try:
            user = await bot.fetch_user(discord_id)
            if user and user.display_avatar:
                embed.set_thumbnail(url=str(user.display_avatar.url))
        except Exception:
            pass
        await ch.send(embed=embed)
        print(f"log_key_channel: gonderildi ({username})")
    except discord.Forbidden:
        print(f"log_key_channel: IZIN HATASI ID={KEY_LOG_CHANNEL_ID}")
    except discord.NotFound:
        print(f"log_key_channel: KANAL YOK ID={KEY_LOG_CHANNEL_ID}")
    except Exception as e:
        print(f"Key kanal hata: {type(e).__name__}: {e}")

async def update_active_channel():
    try:
        ch = bot.get_channel(ACTIVE_CHANNEL_ID)
        if not ch:
            ch = await bot.fetch_channel(ACTIVE_CHANNEL_ID)
        if not ch:
            print(f"update_active_channel: kanal bulunamadi ID={ACTIVE_CHANNEL_ID}")
            return
        _cleanup_expired()
        with _key_lock:
            active = len(_key_store)
        embed = discord.Embed(title="Anlik Aktif Kullanicilar", color=0x57f287)
        embed.add_field(name="Su An Sitede", value=f"**{active}** kisi", inline=True)
        embed.add_field(name="Guncelleme",   value=datetime.now().strftime("%H:%M:%S"), inline=True)
        embed.set_footer(text="Hapsetme Sorgu - Gercek zamanli")
        async for msg in ch.history(limit=5):
            if msg.author == bot.user:
                await msg.delete()
                break
        await ch.send(embed=embed)
    except discord.Forbidden:
        print(f"update_active_channel: IZIN HATASI ID={ACTIVE_CHANNEL_ID}")
    except Exception as e:
        print(f"Aktif kanal hata: {type(e).__name__}: {e}")

async def _fetch_avatar(discord_id: int) -> str:
    try:
        user = await bot.fetch_user(discord_id)
        if user and user.display_avatar:
            return str(user.display_avatar.url)
        return f"https://cdn.discordapp.com/embed/avatars/{discord_id % 5}.png"
    except Exception:
        return f"https://cdn.discordapp.com/embed/avatars/0.png"

# ============================================================
# FLASK ENDPOINT'LER
# ============================================================
ALLOWED_DOMAINS = ["arastir-01.site", "arastir.vip", "bedavasorgupanel.netlify.app"]

# ââ Guvenlik yardimcilari ââ
_rate_store: dict = {}
_rate_lock = threading.Lock()

def _rate_limit(key: str, max_calls: int, window_secs: int) -> bool:
    now = time.time()
    with _rate_lock:
        calls = _rate_store.get(key, [])
        calls = [t for t in calls if now - t < window_secs]
        if len(calls) >= max_calls:
            _rate_store[key] = calls
            return False
        calls.append(now)
        _rate_store[key] = calls
    return True

def _cleanup_rate_store():
    now = time.time()
    with _rate_lock:
        dead = [k for k, v in _rate_store.items() if not v or now - max(v) > 600]
        for k in dead:
            del _rate_store[k]

def _rate_cleanup_loop():
    while True:
        time.sleep(300)
        _cleanup_rate_store()

threading.Thread(target=_rate_cleanup_loop, daemon=True).start()

def _get_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()

# Localhost'a izin vermek icin ALLOW_LOCALHOST=true env var kullan (production'da false)
_ALLOW_LOCALHOST = os.environ.get("ALLOW_LOCALHOST", "").lower() == "true"
# Discord botu icin API key — bot bu header ile istek atar, Railway'de BOT_API_KEY env var ekle
BOT_API_KEY = os.environ.get("BOT_API_KEY", "")

def _check_origin() -> bool:
    # Discord botu icin API key kontrolu
    if BOT_API_KEY and request.headers.get("X-Bot-Key") == BOT_API_KEY:
        return True
    from urllib.parse import urlparse as _up
    origin  = request.headers.get("Origin",  "").strip().rstrip("/")
    referer = request.headers.get("Referer", "").strip().rstrip("/")
    allowed = list(_ALLOWED_ORIGINS)
    if _ALLOW_LOCALHOST:
        allowed += ["http://localhost", "http://127.0.0.1"]

    # file:// veya null origin (local test) - sadece ALLOW_LOCALHOST=true ise
    if _ALLOW_LOCALHOST and (origin == "null" or origin == ""):
        return True

    def _exact(header_val: str) -> bool:
        if not header_val:
            return False
        parsed = _up(header_val)
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return base in allowed

    return _exact(origin) or _exact(referer)

@app.route("/generate-key", methods=["POST"])
def generate_key():
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    # IP bazli: 5 dakikada max 3 key istegi
    if not _rate_limit(f"genkey:{ip}", 3, 300):
        return jsonify({"success": False, "error": "Cok fazla istek. 5 dakika bekleyin."}), 429

    data  = request.get_json(silent=True) or {}
    did   = str(data.get("discord_id", "")).strip()
    uname = str(data.get("username",   "")).strip()

    if not did or not did.isdigit() or not (17 <= len(did) <= 19):
        return jsonify({"success": False, "error": "Gecersiz Discord ID"}), 400
    if not uname or len(uname) < 2:
        return jsonify({"success": False, "error": "Gecersiz kullanici adi"}), 400
    if len(uname) > 64:
        return jsonify({"success": False, "error": "Kullanici adi cok uzun"}), 400
    if _is_banned(did):
        return jsonify({"success": False, "error": "Eririminiz engellenmistir."}), 403
    # Discord ID bazli rate limit: ayni ID 5 dakikada 3'ten fazla key isteyemesin
    if not _rate_limit(f"genkey_did:{did}", 3, 300):
        return jsonify({"success": False, "error": "Cok fazla anahtar istegi. 5 dakika bekleyin."}), 429

    with _maint_lock:
        maint_active = _maintenance["active"]
        maint_msg    = _maintenance["message"]
    if maint_active:
        return jsonify({"success": False, "error": maint_msg, "maintenance": True}), 503

    _cleanup_expired()
    _reset_stats()

    key     = _generate_key()
    expires = time.time() + KEY_EXPIRE_SECS

    with _key_lock:
        _key_store[did] = {"key": key, "username": uname, "expires": expires}
    _save_keys()

    with _stats_lock:
        _stats["keys_today"] += 1
        if did not in _stats["unique_today"]:
            _stats["unique_today"].append(did)
    _save_stats()

    loop = bot.loop
    dm_ok = False
    if loop and loop.is_running():
        try:
            dm_ok = asyncio.run_coroutine_threadsafe(send_dm_key(int(did), uname, key), loop).result(timeout=10)
        except Exception:
            dm_ok = False
        asyncio.run_coroutine_threadsafe(log_webhook(int(did), uname, key), loop)
        asyncio.run_coroutine_threadsafe(log_key_channel(int(did), uname, key), loop)
        asyncio.run_coroutine_threadsafe(update_active_channel(), loop)

    if not dm_ok:
        return jsonify({"success": False, "error": "DM gonderilemedi. Bot ile ortak sunucunuz yok veya DM kapali."}), 400

    return jsonify({"success": True, "message": "Anahtar Discord DM'inize gonderildi.", "expires_in": KEY_EXPIRE_SECS})


@app.route("/verify-key", methods=["POST"])
def verify_key():
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    # IP bazli: 1 dakikada max 10 deneme
    if not _rate_limit(f"verify:{ip}", 10, 60):
        return jsonify({"success": False, "error": "Cok fazla deneme. Bekleyin."}), 429

    data   = request.get_json(silent=True) or {}
    did    = str(data.get("discord_id", "")).strip()
    key_in = str(data.get("key", "")).strip().upper()

    if not did or not key_in:
        return jsonify({"success": False, "error": "Eksik parametre"}), 400
    if _is_banned(did):
        return jsonify({"success": False, "error": "Eririminiz engellenmistir."}), 403
    # Discord ID bazli brute force engelle
    if not _rate_limit(f"verify_did:{did}", 10, 60):
        return jsonify({"success": False, "error": "Cok fazla deneme."}), 429

    _cleanup_expired()
    with _key_lock:
        entry = _key_store.get(did)

    if not entry:
        return jsonify({"success": False, "error": "Key bulunamadi veya suresi doldu"}), 401
    if time.time() > entry["expires"]:
        with _key_lock:
            _key_store.pop(did, None)
        return jsonify({"success": False, "error": "Key suresi doldu", "expired": True}), 401
    if entry["key"] != key_in:
        return jsonify({"success": False, "error": "Gecersiz key"}), 401

    remaining = int(entry["expires"] - time.time())
    return jsonify({"success": True, "username": entry["username"], "remaining": remaining})


@app.route("/check-key", methods=["POST"])
def check_key():
    if not _check_origin():
        return jsonify({"valid": False, "expired": True}), 403
    ip = _get_ip()
    if not _rate_limit(f"checkkey:{ip}", 20, 60):
        return jsonify({"valid": False, "expired": True}), 429
    data   = request.get_json(silent=True) or {}
    did    = str(data.get("discord_id", "")).strip()
    key_in = str(data.get("key", "")).strip().upper()

    _cleanup_expired()
    with _key_lock:
        entry = _key_store.get(did)

    if not entry or entry["key"] != key_in or time.time() > entry["expires"]:
        return jsonify({"valid": False, "expired": True})

    return jsonify({"valid": True, "remaining": int(entry["expires"] - time.time())})


@app.route("/kredi-kontrol", methods=["POST"])
def kredi_kontrol():
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"kredi:{ip}", 30, 60):
        return jsonify({"success": False, "error": "Cok fazla istek."}), 429
    data = request.get_json(silent=True) or {}
    did  = str(data.get("discord_id", "")).strip()
    if not did or not did.isdigit() or not (17 <= len(did) <= 19):
        return jsonify({"success": False, "error": "Gecersiz ID"}), 400

    credits = _get_credits(did)
    is_new  = credits == -1
    renewed = 0

    if is_new:
        _set_credits(did, INITIAL_CREDITS)
        credits = INITIAL_CREDITS
        with _reset_lock:
            _credit_reset_times[did] = time.time()
        _save_credit_reset()
    else:
        renewed = _check_and_reset_credit(did)
        if renewed > 0:
            credits = _get_credits(did)

    return jsonify({"success": True, "credits": credits, "is_new": is_new, "renewed": renewed})


@app.route("/kredi-harca", methods=["POST"])
def kredi_harca():
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    # 1 dakikada max 30 harcama (toplu sorgu icin yeterli)
    if not _rate_limit(f"harca:{ip}", 30, 60):
        return jsonify({"success": False, "error": "Cok hizli istek."}), 429
    data = request.get_json(silent=True) or {}
    did  = str(data.get("discord_id", "")).strip()
    if not did or not did.isdigit() or not (17 <= len(did) <= 19):
        return jsonify({"success": False, "error": "Gecersiz ID"}), 400

    ok = _spend_credit(did)
    if not ok:
        return jsonify({"success": False, "error": "Krediniz bitti!", "credits": 0})

    remaining = _get_credits(did)
    return jsonify({"success": True, "credits": remaining})


@app.route("/transfer-kredi", methods=["POST"])
def transfer_kredi():
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"transfer:{ip}", 5, 60):
        return jsonify({"success": False, "error": "Cok fazla transfer denemesi."}), 429
    data     = request.get_json(silent=True) or {}
    from_id  = str(data.get("from_id", "")).strip()
    to_id    = str(data.get("to_id",   "")).strip()
    try:
        amount = int(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Gecersiz miktar"}), 400

    if not from_id or not from_id.isdigit() or not (17 <= len(from_id) <= 19):
        return jsonify({"success": False, "error": "Gecersiz gonderen ID"}), 400
    if not to_id or not to_id.isdigit() or not (17 <= len(to_id) <= 19):
        return jsonify({"success": False, "error": "Gecersiz alici ID"}), 400
    if from_id == to_id:
        return jsonify({"success": False, "error": "Kendinize transfer yapamazsiniz"}), 400
    if amount <= 0:
        return jsonify({"success": False, "error": "Gecersiz miktar"}), 400
    if amount > 100:
        return jsonify({"success": False, "error": "Maksimum 100 kredi transfer edebilirsiniz"}), 400

    from_credits = _get_credits(from_id)
    from_credits = max(0, from_credits if from_credits >= 0 else 0)
    if from_credits < amount:
        return jsonify({"success": False, "error": f"Yetersiz kredi. Bakiyeniz: {from_credits}"}), 400

    # Race condition onleme: once from_id'den dusur, sonra to_id'ye ekle
    with _credits_lock:
        actual = _credits.get(str(from_id), 0)
        if actual < amount:
            return jsonify({"success": False, "error": f"Yetersiz kredi. Bakiyeniz: {actual}"}), 400
        _credits[str(from_id)] = actual - amount
        _credits[str(to_id)]   = _credits.get(str(to_id), 0) + amount
        new_from = _credits[str(from_id)]
        new_to   = _credits[str(to_id)]
    _save_credits()

    return jsonify({"success": True, "from_credits": new_from, "to_credits": new_to})


@app.route("/announcement", methods=["GET"])
def get_announcement():
    ip = _get_ip()
    if not _rate_limit(f"ann:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    with _ann_lock:
        return jsonify({"active": _announcement["active"], "text": _announcement["text"]})


@app.route("/maintenance", methods=["GET"])
def get_maintenance():
    ip = _get_ip()
    if not _rate_limit(f"maint:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    with _maint_lock:
        return jsonify({"active": _maintenance["active"], "message": _maintenance["message"]})


@app.route("/yt-config", methods=["GET"])
def get_yt_config():
    ip = _get_ip()
    if not _rate_limit(f"yt:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    with _yt_lock:
        return jsonify({"url": _yt_config["url"], "id": _yt_config["id"], "active": bool(_yt_config["url"])})


@app.route("/settings", methods=["GET"])
def get_settings():
    ip = _get_ip()
    if not _rate_limit(f"settings:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    with _settings_lock:
        return jsonify({"key_expire_secs": _settings["key_expire_secs"], "yt_wait_secs": _settings["yt_wait_secs"]})


@app.route("/avatar/<discord_id>")
def get_avatar(discord_id):
    if not _check_origin():
        return jsonify({"error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"avatar:{ip}", 20, 60):
        return jsonify({"error": "Cok fazla istek."}), 429
    if not discord_id.isdigit() or not (15 <= len(discord_id) <= 20):
        return jsonify({"error": "Gecersiz ID"}), 400
    try:
        loop = bot.loop
        if loop and loop.is_running():
            avatar_url = asyncio.run_coroutine_threadsafe(_fetch_avatar(int(discord_id)), loop).result(timeout=8)
            return jsonify({"success": True, "avatar_url": avatar_url, "discord_id": discord_id})
        return jsonify({"success": False, "error": "Bot hazir degil"}), 503
    except Exception as e:
        return jsonify({"success": False, "error": "Avatar alinamadi"}), 500


@app.route("/proxy")
def proxy():
    if not _check_origin():
        return jsonify({"error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"proxy:{ip}", 20, 60):
        return jsonify({"error": "Cok fazla istek."}), 429
    b64 = request.args.get("b64")
    if b64:
        try:
            url = base64.b64decode(b64).decode("utf-8")
        except Exception:
            return jsonify({"error": "Gecersiz b64"}), 400
    else:
        url = request.args.get("url")
    if not url:
        return jsonify({"error": "url eksik"}), 400
    from urllib.parse import urlparse as _urlparse
    _parsed = _urlparse(url)
    _hostname = _parsed.hostname or ""
    if not any(_hostname == d or _hostname.endswith("." + d) for d in ALLOWED_DOMAINS):
        return jsonify({"error": "Izin verilmeyen domain"}), 403
    try:
        r = _session.get(url, timeout=20)
        return jsonify(json.loads(r.text))
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"error": "Istek basarisiz"}), 500


@app.route("/log/tr")
def log_tr():
    if not _check_origin():
        return jsonify({"error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"logtr:{ip}", 10, 60):
        return jsonify({"error": "Cok fazla istek."}), 429
    site = request.args.get("site", "").strip()
    if not site:
        return jsonify({"error": "site eksik"}), 400
    # Sadece domain formati kabul et - path traversal ve SSRF engelle
    if len(site) > 253 or not re.match(r'^[a-zA-Z0-9._-]+$', site):
        return jsonify({"error": "Gecersiz site formati"}), 400
    try:
        r = _session.get(f"https://wazely.vercel.app/api/trlog?site={site}", timeout=25)
        try:
            return jsonify(json.loads(r.text))
        except Exception:
            return r.text, 200, {"Content-Type": "application/json"}
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"error": "Istek basarisiz"}), 500


@app.route("/log/global")
def log_global():
    if not _check_origin():
        return jsonify({"error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"logglobal:{ip}", 10, 60):
        return jsonify({"error": "Cok fazla istek."}), 429
    domain = request.args.get("domain", "").strip()
    if not domain:
        return jsonify({"error": "domain eksik"}), 400
    if len(domain) > 253 or not re.match(r'^[a-zA-Z0-9._-]+$', domain):
        return jsonify({"error": "Gecersiz domain formati"}), 400
    try:
        r = _session.get(f"https://wentyn.pythonanywhere.com/extract?domain={domain}", timeout=25)
        return r.text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"error": "Istek basarisiz"}), 500


@app.route("/discord/user/<user_id>")
def discord_user(user_id):
    if not _check_origin():
        return jsonify({"error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"discord:{ip}", 15, 60):
        return jsonify({"error": "Cok fazla istek."}), 429
    if not user_id.isdigit() or not (5 <= len(user_id) <= 30):
        return jsonify({"error": "Gecersiz ID"}), 400
    try:
        r = _session.get(
            f"https://prox0959.netlify.app/api/search?id={user_id}",
            timeout=10
        )
        try:
            return jsonify(json.loads(r.text))
        except Exception:
            return r.text, r.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"error": "Istek basarisiz"}), 500


@app.route("/health")
def health():
    ip = _get_ip()
    if not _rate_limit(f"health:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    return jsonify({"status": "ok", "service": "Hapsetme Sorgu v2"})


@app.route("/")
def index():
    ip = _get_ip()
    if not _rate_limit(f"root:{ip}", 30, 60):
        return jsonify({"error": "Cok fazla istek"}), 429
    return jsonify({"status": "ok", "service": "Hapsetme Sorgu Backend v2"})


# ââ Global hata yakalayici - ic bilgi sizdirma ââ
@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Gecersiz istek"}), 400

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Bulunamadi"}), 404

@app.errorhandler(405)
def method_not_allowed(e):
    return jsonify({"error": "Metot desteklenmiyor"}), 405

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"error": "Istek cok buyuk (max 1MB)"}), 413

@app.errorhandler(429)
def too_many_requests(e):
    return jsonify({"error": "Cok fazla istek, bekleyin"}), 429

@app.errorhandler(500)
def internal_error(e):
    # Not: Bu handler abort(500) icin; unhandled_exception yakalanmamis hatalar icin
    print(f"500 hata: {e}")
    return jsonify({"error": "Sunucu hatasi"}), 500

@app.errorhandler(Exception)
def unhandled_exception(e):
    print(f"Yakalanmamis istisna: {type(e).__name__}: {e}")
    return jsonify({"error": "Sunucu hatasi"}), 500


# ââ /hslog - JS'den webhook yerine buraya gonder ââ
@app.route("/hslog", methods=["POST"])
def hslog():
    """JS'deki hsLog() buraya istek atar, server.py Discord'a iletir."""
    if not _check_origin():
        return jsonify({"ok": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"hslog:{ip}", 30, 60):
        return jsonify({"ok": False}), 429
    data = request.get_json(silent=True) or {}
    wh = WEBHOOK_LOG
    if not wh:
        return jsonify({"ok": False}), 503
    try:
        _session.post(wh, json=data, timeout=8)
        return jsonify({"ok": True})
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"ok": False, "error": "Istek basarisiz"}), 500


# ââ /hsticket - Ticket webhook ââ
@app.route("/hsticket", methods=["POST"])
def hsticket():
    """Ticket gonderimi - Discord embed olusturarak WEBHOOK_TICKET'e iletir."""
    if not _check_origin():
        return jsonify({"ok": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"ticket:{ip}", 5, 300):
        return jsonify({"ok": False, "error": "Cok fazla ticket. Bekleyin."}), 429
    data = request.get_json(silent=True) or {}
    wh = WEBHOOK_TICKET
    if not wh:
        return jsonify({"ok": False, "error": "Webhook yapilandirilmadi"}), 503

    t_type      = str(data.get("type",    "diger"))[:20]
    title       = str(data.get("title",   "Basliksiz"))[:200]
    body        = str(data.get("body",    ""))[:1000]
    disc_user   = str(data.get("discord", "Bilinmiyor"))[:100]
    ip_addr     = str(data.get("ip",      "-"))[:50]
    country     = str(data.get("country", "-"))[:100]
    city        = str(data.get("city",    "-"))[:100]

    type_labels = {
        "hata":  "Hata Bildirimi",
        "oneri": "Oneri",
        "diger": "Diger",
    }
    colors = {
        "hata":  0xed4245,
        "oneri": 0xfee75c,
        "diger": 0x5865f2,
    }

    embed = {
        "title":  f"{type_labels.get(t_type, 'Diger')} - {title[:200]}",
        "color":  colors.get(t_type, 0x5865f2),
        "fields": [
            {"name": "Aciklama",  "value": body[:1000] or "-",  "inline": False},
            {"name": "Discord",   "value": disc_user,            "inline": True},
            {"name": "IP",        "value": ip_addr,              "inline": True},
            {"name": "Ulke",    "value": country,              "inline": True},
            {"name": "Sehir",   "value": city,                 "inline": True},
            {"name": "Zaman",     "value": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "inline": True},
        ],
        "footer":    {"text": "Hapsetme Ticket"},
        "timestamp": datetime.now().astimezone().isoformat(),
    }

    try:
        r = _session.post(wh, json={"embeds": [embed]}, timeout=8)
        if r.status_code not in (200, 204):
            return jsonify({"ok": False, "error": f"Discord HTTP {r.status_code}"}), 502
        return jsonify({"ok": True})
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"ok": False, "error": "Istek basarisiz"}), 500


# ============================================================
# SEARCHULP PROXY ENDPOINT'LER - API key gizli kalir
# ============================================================
_SEARCHULP_BASE = "https://searchulp.xyz/api"
_SEARCHULP_KEY  = os.environ.get("SEARCHULP_API_KEY", "")

def _searchulp_get(path: str, params: dict = None):
    """searchulp.xyz'e istek at, API key backend'de kalir."""
    if not _check_origin():
        return jsonify({"success": False, "error": "Yetkisiz istek"}), 403
    ip = _get_ip()
    if not _rate_limit(f"api:{ip}", 60, 60):
        return jsonify({"success": False, "error": "Cok fazla istek. 1 dakika bekleyin."}), 429
    if not _SEARCHULP_KEY:
        return jsonify({"success": False, "error": "Servis gecici olarak kullanim disi"}), 503
    p = dict(params or {})
    p["key"] = _SEARCHULP_KEY
    try:
        r = _session.get(f"{_SEARCHULP_BASE}/{path}", params=p, timeout=20)
        try:
            return jsonify(json.loads(r.text))
        except Exception:
            return r.text, 200, {"Content-Type": "application/json"}
    except Exception as e:
        print(f"Hata: {e}"); return jsonify({"success": False, "error": "Istek basarisiz"}), 500

@app.route("/api/tcpro/<tc>")
def api_tcpro(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"tcpro/{tc}")

@app.route("/api/adsoyad")
def api_adsoyad():
    ip = _get_ip()
    if not _rate_limit(f"adsoyad:{ip}", 15, 60):
        return jsonify({"success": False, "error": "Cok fazla istek."}), 429
    p = {}
    for k in ["ad", "soyad", "il", "ilce"]:
        v = request.args.get(k, "").strip()[:50]
        if v: p[k] = v
    if "ad" not in p:
        return jsonify({"success": False, "error": "ad parametresi zorunlu"}), 400
    return _searchulp_get("adsoyad", p)

@app.route("/api/adsoyadpro")
def api_adsoyadpro():
    ip = _get_ip()
    if not _rate_limit(f"adsoyadpro:{ip}", 15, 60):
        return jsonify({"success": False, "error": "Cok fazla istek."}), 429
    p = {}
    for k in ["ad", "soyad"]:
        v = request.args.get(k, "").strip()[:50]
        if v: p[k] = v
    if "ad" not in p:
        return jsonify({"success": False, "error": "ad parametresi zorunlu"}), 400
    return _searchulp_get("adsoyadpro", p)

@app.route("/api/aile/<tc>")
def api_aile(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"aile/{tc}")

@app.route("/api/sulale/<tc>")
def api_sulale(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"sulale/{tc}")

@app.route("/api/cocuk/<tc>")
def api_cocuk(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"cocuk/{tc}")

@app.route("/api/adres/<tc>")
def api_adres(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"adres/{tc}")

@app.route("/api/gsmtc/<gsm>")
def api_gsmtc(gsm):
    if not gsm.isdigit() or not (10 <= len(gsm) <= 11):
        return jsonify({"success": False, "error": "Gecersiz GSM"}), 400
    return _searchulp_get(f"gsmtc/{gsm}")

@app.route("/api/tcgsm/<tc>")
def api_tcgsm(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"tcgsm/{tc}")

@app.route("/api/isyeri/<tc>")
def api_isyeri(tc):
    if not tc.isdigit() or len(tc) != 11:
        return jsonify({"success": False, "error": "Gecersiz TC"}), 400
    return _searchulp_get(f"isyeri/{tc}")


# ============================================================
# BOT EVENTS
# ============================================================
@bot.event
async def on_ready():
    print(f"Discord bot hazir: {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.playing,
            name="/bakiye - kredinize bakmak icin kullanabilirsiniz"
        )
    )
    _load_keys()
    _load_blacklist()
    _load_announcement()
    _load_stats()
    _load_yt()
    _load_settings()
    _load_maintenance()
    # RESET_MAINTENANCE=true env var varsa bakimi kapat (stuck deployment icin)
    if os.environ.get("RESET_MAINTENANCE", "").lower() == "true":
        with _maint_lock:
            _maintenance["active"] = False
        _save_maintenance()
        print("BAKIM MODU SIFIRLANDI (RESET_MAINTENANCE=true)")
    _load_credits()
    _load_credit_reset()
    if not daily_report.is_running():
        daily_report.start()
    if not afk_credit_task.is_running():
        afk_credit_task.start()
    if not nightly_credit_reset.is_running():
        nightly_credit_reset.start()

    global AFK_GUILD_ID
    try:
        afk_ch = await bot.fetch_channel(AFK_CHANNEL_ID)
        if afk_ch and hasattr(afk_ch, 'guild') and afk_ch.guild:
            AFK_GUILD_ID = afk_ch.guild.id
            print(f"AFK kanali onaylandi: {afk_ch.name} ({AFK_CHANNEL_ID})")
        else:
            print(f"AFK kanali bulunamadi ID={AFK_CHANNEL_ID}")
    except Exception as e:
        print(f"AFK kanal fetch hata: {e}")


@bot.event
async def on_member_join(member: discord.Member):
    try:
        did = str(member.id)
        if _get_credits(did) == -1:
            _set_credits(did, INITIAL_CREDITS)
            print(f"Yeni uye: {member.name} - {INITIAL_CREDITS} kredi verildi")
        embed = discord.Embed(
            title="Hapsetme Sorgu'ya Hos Geldin!",
            description=f"Merhaba **{member.name}**!\n\nHapsetme Sorgu platformuna erisim icin adimları takip et:",
            color=0x6366f1,
        )
        embed.add_field(name="Site",   value="[bedavasorgupanel.netlify.app](https://bedavasorgupanel.netlify.app)", inline=False)
        embed.add_field(name="Giris",  value="1. Siteye git\n2. Discord adi + ID gir\n3. 'Anahtar Iste' tikla\n4. Bu DM'e gelen anahtari gir\n5. Sorgu yap!", inline=False)
        embed.add_field(name="ID Nasil?", value="Ayarlar > Gelismis > Gelistirici Modu > Kullanici adina sag tikla > ID Kopyala", inline=False)
        embed.set_footer(text="Hapsetme Sorgu - Anahtar her 20 dakikada yenilenir")
        if member.display_avatar:
            embed.set_thumbnail(url=member.display_avatar.url)
        await member.send(embed=embed)
    except Exception as e:
        print(f"Hos geldin DM hatasi ({member.name}): {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    if AFK_CHANNEL_ID and after.channel and after.channel.id == AFK_CHANNEL_ID:
        print(f"AFK kanalina girdi: {member.name} ({member.id})")
    elif AFK_CHANNEL_ID and before.channel and before.channel.id == AFK_CHANNEL_ID:
        print(f"AFK kanalinden cikti: {member.name} ({member.id})")


# ============================================================
# BOT TASKS
# ============================================================
@tasks.loop(seconds=AFK_INTERVAL_SECS)
async def afk_credit_task():
    try:
        ch = bot.get_channel(AFK_CHANNEL_ID)
        if not ch:
            ch = await bot.fetch_channel(AFK_CHANNEL_ID)
        if not ch or not isinstance(ch, discord.VoiceChannel):
            print(f"AFK task: kanal bulunamadi veya ses kanali degil ID={AFK_CHANNEL_ID}")
            return
        members_in_channel = [m for m in ch.members if not m.bot]
        print(f"AFK task: {len(members_in_channel)} kisi kanalda")
        for member in members_in_channel:
            did = str(member.id)
            new_credits = _add_credits(did, AFK_CREDIT_GAIN)
            print(f"AFK kredi: {member.name} +{AFK_CREDIT_GAIN} (toplam: {new_credits})")
            try:
                await member.send(
                    f"**+{AFK_CREDIT_GAIN} Kredi Kazandiniz!**\n"
                    f"AFK kanalinda 5 dakika kaldiniz.\n"
                    f"Toplam krediniz: **{new_credits}**"
                )
            except Exception:
                pass
    except discord.Forbidden:
        print(f"AFK task: IZIN HATASI ID={AFK_CHANNEL_ID}")
    except discord.NotFound:
        print(f"AFK task: KANAL YOK ID={AFK_CHANNEL_ID}")
    except Exception as e:
        print(f"AFK task hata: {type(e).__name__}: {e}")

@afk_credit_task.before_loop
async def before_afk():
    await bot.wait_until_ready()
    print("AFK kredi task hazir.")


@tasks.loop(hours=24)
async def daily_report():
    try:
        ch = bot.get_channel(REPORT_CHANNEL_ID)
        if not ch:
            ch = await bot.fetch_channel(REPORT_CHANNEL_ID)
        if not ch:
            return
        with _stats_lock:
            keys_today   = _stats["keys_today"]
            unique_today = len(_stats["unique_today"])
            day          = _stats["day"]
        with _key_lock:
            active_now = len(_key_store)
        with _bl_lock:
            banned = len(_blacklist)
        embed = discord.Embed(title=f"Gunluk Rapor - {day}", color=0x6366f1)
        embed.add_field(name="Uretilen Key",    value=str(keys_today),   inline=True)
        embed.add_field(name="Tekil Kullanici", value=str(unique_today), inline=True)
        embed.add_field(name="Su An Aktif",     value=str(active_now),   inline=True)
        embed.add_field(name="Kara Listede",    value=str(banned),       inline=True)
        embed.set_footer(text="Hapsetme Sorgu - Otomatik Rapor")
        embed.timestamp = discord.utils.utcnow()
        await ch.send(embed=embed)
        with _stats_lock:
            _stats["keys_today"]   = 0
            _stats["unique_today"] = []
            _stats["day"]          = datetime.now().strftime("%Y-%m-%d")
        _save_stats()
    except Exception as e:
        print(f"Gunluk rapor hata: {e}")

@daily_report.before_loop
async def before_report():
    await bot.wait_until_ready()
    now  = datetime.now()
    secs = ((24 - now.hour - 1) * 3600) + ((60 - now.minute - 1) * 60) + (60 - now.second)
    print(f"Rapor {secs//3600}s sonra baslar.")
    await asyncio.sleep(secs)


@tasks.loop(hours=24)
async def nightly_credit_reset():
    now = time.time()
    # Snapshot al, lock diÅŸinda iÅŸle - deadlock engelle
    with _credits_lock:
        users = dict(_credits)
    refilled = 0
    updates = {}
    for did, amount in users.items():
        if amount <= 0:
            updates[did] = INITIAL_CREDITS
            refilled += 1
    if updates:
        with _credits_lock:
            for did, val in updates.items():
                _credits[did] = val
        with _reset_lock:
            for did in updates:
                _credit_reset_times[did] = now
        _save_credits()
        _save_credit_reset()
    print(f"Gece kredi yenileme: {refilled} kullaniciya {INITIAL_CREDITS} kredi verildi.")
    try:
        ch = bot.get_channel(REPORT_CHANNEL_ID)
        if not ch:
            ch = await bot.fetch_channel(REPORT_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Gece Kredi Yenileme", color=0xf59e0b, timestamp=discord.utils.utcnow())
            embed.add_field(name="Yenilenen", value=f"{refilled} kullanici",         inline=True)
            embed.add_field(name="Verilen",   value=f"{INITIAL_CREDITS} kredi/kisi", inline=True)
            embed.add_field(name="Toplam",    value=f"{len(users)} kayitli",         inline=True)
            embed.set_footer(text="Hapsetme Sorgu - Otomatik Kredi Yenileme")
            await ch.send(embed=embed)
    except Exception as e:
        print(f"Kredi yenileme rapor hata: {e}")

@nightly_credit_reset.before_loop
async def before_credit_reset():
    await bot.wait_until_ready()
    now  = datetime.now()
    secs = ((24 - now.hour - 1) * 3600) + ((60 - now.minute - 1) * 60) + (60 - now.second)
    print(f"Kredi yenileme {secs//3600}s sonra baslar.")
    await asyncio.sleep(secs)


# ============================================================
# BOT KOMUTLARI
# ============================================================
@bot.command(name="krediherkese")
async def krediherkese_cmd(ctx, miktar: str = ""):
    if not _is_admin(ctx): return
    if not miktar or not miktar.lstrip("-").isdigit():
        await ctx.send("Kullanim: /krediherkese <miktar>\nOrnek: /krediherkese 5"); return
    amount = int(miktar)
    # Snapshot al, lock diÅŸinda guncelle - deadlock engelle
    with _credits_lock:
        user_ids = list(_credits.keys())
    if not user_ids:
        await ctx.send("Hic kayitli kullanici yok."); return
    with _credits_lock:
        for did in user_ids:
            cur = _credits.get(did, 0)
            _credits[did] = max(0, cur + amount)
    _save_credits()
    action = f"+{amount}" if amount >= 0 else str(amount)
    await ctx.send(f"Tum **{len(user_ids)}** kullaniciya **{action}** kredi yapildi.")

@bot.command(name="istatistik")
async def istatistik_cmd(ctx):
    if not _is_admin(ctx): return
    _cleanup_expired()
    with _stats_lock:
        keys_today   = _stats["keys_today"]
        unique_today = len(_stats["unique_today"])
        day          = _stats["day"]
    with _key_lock:
        active_now = len(_key_store)
    with _credits_lock:
        total_users   = len(_credits)
        total_credits = sum(_credits.values())
        zero_credits  = sum(1 for v in _credits.values() if v <= 0)
    with _bl_lock:
        banned = len(_blacklist)
    embed = discord.Embed(title=f"Istatistik - {day}", color=0x6366f1)
    embed.add_field(name="Bugun Uretilen Key", value=str(keys_today),   inline=True)
    embed.add_field(name="Bugun Tekil Giris",  value=str(unique_today), inline=True)
    embed.add_field(name="Su An Aktif",        value=str(active_now),   inline=True)
    embed.add_field(name="Toplam Kayitli",     value=str(total_users),  inline=True)
    embed.add_field(name="Toplam Kredi",       value=str(total_credits), inline=True)
    embed.add_field(name="Kredisi 0 Olan",     value=str(zero_credits), inline=True)
    embed.add_field(name="Kara Listede",       value=str(banned),       inline=True)
    embed.set_footer(text="Hapsetme Sorgu - Canli Istatistik")
    await ctx.send(embed=embed)

@bot.command(name="kredisil")
async def kredisil_cmd(ctx, hedef_id: str = "", miktar: str = ""):
    if not _is_admin(ctx): return
    if not hedef_id or not hedef_id.isdigit():
        await ctx.send("Kullanim: /kredisil <discord_id> <miktar>"); return
    if not miktar or not miktar.isdigit():
        await ctx.send("Gecersiz miktar. Pozitif sayi girin."); return
    amount = int(miktar)
    if amount <= 0:
        await ctx.send("Miktar 0'dan buyuk olmali."); return
    cur = _get_credits(hedef_id)
    if cur == -1:
        await ctx.send(f"**{hedef_id}** - Kayit yok."); return
    new = max(0, cur - amount)
    _set_credits(hedef_id, new)
    await ctx.send(f"**{hedef_id}** kullanicisinden **{amount}** kredi silindi.\nEski: {cur} â†’ Yeni: **{new}**")
    try:
        user = await bot.fetch_user(int(hedef_id))
        if user:
            await user.send(f"**Kredinizden {amount} silindi.**\nYeni bakiyeniz: **{new} kredi**")
    except Exception:
        pass

@bot.command(name="kredigor")
async def kredigor_cmd(ctx, hedef_id: str = ""):
    if not _is_admin(ctx): return
    if not hedef_id or not hedef_id.isdigit():
        await ctx.send("Kullanim: /kredigor <discord_id>"); return
    cur = _get_credits(hedef_id)
    if cur == -1:
        await ctx.send(f"**{hedef_id}** - Henuz kayit yok")
    else:
        await ctx.send(f"**{hedef_id}** kullanicisinin kredisi: **{cur}**")

@bot.command(name="topkredi")
async def topkredi_cmd(ctx):
    if not _is_admin(ctx): return
    with _credits_lock:
        users = dict(_credits)
    if not users:
        await ctx.send("Hic kayitli kullanici yok."); return
    sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="Top 10 Kredi", color=0xf59e0b)
    lines = []
    for rank, (did, amount) in enumerate(sorted_users, 1):
        medal = ["1.", "2.", "3."][rank-1] if rank <= 3 else f"{rank}."
        lines.append(f"{medal} `{did}` - **{amount} kredi**")
    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Toplam {len(users)} kayitli kullanici")
    await ctx.send(embed=embed)

@bot.command(name="duyurudm")
async def duyurudm_cmd(ctx, *, mesaj: str = ""):
    if not _is_admin(ctx): return
    if not mesaj:
        await ctx.send("Kullanim: /duyurudm <mesaj>"); return
    with _key_lock:
        key_users = list(_key_store.keys())
    with _credits_lock:
        credit_users = list(_credits.keys())
    all_ids = list(set(key_users + credit_users))
    if not all_ids:
        await ctx.send("Hic kayitli kullanici yok."); return
    await ctx.send(f"{len(all_ids)} kullaniciya DM gonderiliyor...")
    sent = 0; failed = 0
    for did in all_ids:
        try:
            user = await bot.fetch_user(int(did))
            embed = discord.Embed(title="Hapsetme Sorgu - Duyuru", description=mesaj, color=0x6366f1)
            embed.set_footer(text="Hapsetme Sorgu Ekibi")
            await user.send(embed=embed)
            sent += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1
    await ctx.send(f"Tamamlandi! Gonderilen: **{sent}** | Basarisiz: **{failed}**")

@bot.command(name="kredi")
async def kredi_cmd(ctx, hedef_id: str = "", miktar: str = ""):
    if not _is_admin(ctx): return
    if not hedef_id or not hedef_id.isdigit():
        await ctx.send("Kullanim: /kredi <discord_id> <miktar>"); return
    if not miktar or not miktar.lstrip("-").isdigit():
        await ctx.send("Gecersiz miktar."); return
    amount = int(miktar)
    cur = _get_credits(hedef_id)
    if cur == -1: cur = 0
    new = max(0, cur + amount)
    _set_credits(hedef_id, new)
    action = f"+{amount}" if amount >= 0 else str(amount)
    await ctx.send(f"**{hedef_id}** kullanicisina {action} kredi yapildi.\nYeni bakiye: **{new} kredi**")
    try:
        user = await bot.fetch_user(int(hedef_id))
        if user:
            await user.send(f"**Krediniz Guncellendi!**\nAdmin tarafindan {action} kredi yapildi.\nYeni bakiyeniz: **{new} kredi**")
    except Exception:
        pass

@bot.command(name="bakiye")
async def bakiye_cmd(ctx):
    if not isinstance(ctx.channel, discord.DMChannel): return
    did = str(ctx.author.id)
    cur = _get_credits(did)
    if cur == -1:
        await ctx.send("Henuz krediniz yok. Siteye giris yaparak 10 baslangic kredisi kazanabilirsiniz.")
    else:
        await ctx.send(
            f"**Kredi Bakiyeniz: {cur}**\n"
            f"Her sorgu 1 kredi harcar.\n"
            f"Kredi kazanmak icin: {CREDIT_SERVER_INVITE} sunucusundaki **afk** ses kanalinda 5 dakika kalin."
        )

@bot.command(name="krediyenile")
async def krediyenile_cmd(ctx):
    if not _is_admin(ctx): return
    await ctx.send("Kredi yenileniyor...")
    with _credits_lock:
        users = dict(_credits)
    for did in users:
        with _credits_lock:
            _credits[did] = _credits.get(did, 0) + INITIAL_CREDITS
    _save_credits()
    await ctx.send(f"Tum **{len(users)}** kullaniciya **+{INITIAL_CREDITS}** kredi eklendi.")

@bot.command(name="kredisifirla")
async def kredisifirla_cmd(ctx):
    if not _is_admin(ctx): return
    with _credits_lock:
        count = len(_credits)
        for did in _credits:
            _credits[did] = 0
    _save_credits()
    await ctx.send(f"**{count}** kullanicinin kredisi **0** yapildi.")
@bot.command(name="ban")
async def ban_cmd(ctx, target_id: str = ""):
    if not _is_admin(ctx): return
    if not target_id or not target_id.isdigit():
        await ctx.send("Kullanim: /ban <discord_id>"); return
    with _bl_lock: _blacklist.add(target_id)
    _save_blacklist()
    with _key_lock: _key_store.pop(target_id, None)
    _save_keys()
    await ctx.send(f"**{target_id}** kara listeye eklendi ve key'i silindi.")

@bot.command(name="unban")
async def unban_cmd(ctx, target_id: str = ""):
    if not _is_admin(ctx): return
    if not target_id or not target_id.isdigit():
        await ctx.send("Kullanim: /unban <discord_id>"); return
    with _bl_lock: _blacklist.discard(target_id)
    _save_blacklist()
    await ctx.send(f"**{target_id}** kara listeden cikarildi.")

@bot.command(name="liste")
async def liste_cmd(ctx):
    if not _is_admin(ctx): return
    with _bl_lock: bl = list(_blacklist)
    if not bl: await ctx.send("Kara liste bos.")
    else: await ctx.send(f"**Kara Liste ({len(bl)} kisi):**\n" + "\n".join(bl))

@bot.command(name="aktif")
async def aktif_cmd(ctx):
    if not _is_admin(ctx): return
    _cleanup_expired()
    with _key_lock: entries = dict(_key_store)
    if not entries:
        await ctx.send("Su an aktif kullanici yok."); return
    lines = []
    for did, v in entries.items():
        rem = max(0, int(v["expires"] - time.time()))
        lines.append(f"`{did}` - **{v['username']}** - {rem//60:02d}:{rem%60:02d} kaldi")
    embed = discord.Embed(title=f"Aktif Kullanicilar ({len(entries)} kisi)", color=0x57f287)
    embed.description = "\n".join(lines[:20])
    if len(lines) > 20: embed.set_footer(text=f"+{len(lines)-20} kisi daha")
    await ctx.send(embed=embed)

@bot.command(name="duyuru")
async def duyuru_cmd(ctx, *, mesaj: str = ""):
    if not _is_admin(ctx): return
    if not mesaj:
        with _ann_lock: _announcement["active"] = False; _announcement["text"] = ""
        _save_announcement()
        await ctx.send("Duyuru kapatildi."); return
    with _ann_lock: _announcement["active"] = True; _announcement["text"] = mesaj
    _save_announcement()
    await ctx.send(f"Duyuru ayarlandi:\n> {mesaj}")

@bot.command(name="youtube")
async def youtube_cmd(ctx, *, url: str = ""):
    if not _is_admin(ctx): return
    if not url:
        with _yt_lock: cur = _yt_config["url"] or "Ayarlanmamis"
        await ctx.send(f"Mevcut video: {cur}\nDegistirmek: /youtube <url>"); return
    vid = _extract_yt_id(url)
    if not vid:
        await ctx.send("Gecersiz YouTube URL'si."); return
    with _yt_lock:
        _yt_config["url"] = url
        _yt_config["id"]  = vid
    _save_yt()
    await ctx.send(f"YouTube videosu guncellendi!\nURL: {url}\nID: `{vid}`")

@bot.command(name="sure")
async def sure_cmd(ctx, dakika: str = ""):
    if not _is_admin(ctx): return
    if not dakika or not dakika.isdigit():
        with _settings_lock: cur = _settings["key_expire_secs"] // 60
        await ctx.send(f"Mevcut key suresi: **{cur} dakika**\nDegistirmek: /sure <dakika>"); return
    mins = int(dakika)
    if mins < 5 or mins > 1440:
        await ctx.send("5 ile 1440 dakika arasi olmali."); return
    with _settings_lock: _settings["key_expire_secs"] = mins * 60
    global KEY_EXPIRE_SECS
    KEY_EXPIRE_SECS = mins * 60
    _save_settings()
    await ctx.send(f"Key suresi **{mins} dakika** yapildi.")

@bot.command(name="bekleme")
async def bekleme_cmd(ctx, saniye: str = ""):
    if not _is_admin(ctx): return
    if not saniye or not saniye.isdigit():
        with _settings_lock: cur = _settings["yt_wait_secs"]
        await ctx.send(f"Mevcut YT bekleme: **{cur} saniye**\nDegistirmek: /bekleme <saniye>"); return
    secs = int(saniye)
    if secs < 1 or secs > 60:
        await ctx.send("1 ile 60 saniye arasi olmali."); return
    with _settings_lock: _settings["yt_wait_secs"] = secs
    _save_settings()
    await ctx.send(f"YT bekleme **{secs} saniye** yapildi.")

@bot.command(name="bakim")
async def bakim_cmd(ctx, durum: str = "", *, mesaj: str = ""):
    if not _is_admin(ctx): return
    if durum.lower() == "ac":
        with _maint_lock:
            _maintenance["active"] = True
            if mesaj: _maintenance["message"] = mesaj
        _save_maintenance()
        await ctx.send(f"Bakim modu **ACILDI**.\nMesaj: {_maintenance['message']}")
    elif durum.lower() == "kapat":
        with _maint_lock: _maintenance["active"] = False
        _save_maintenance()
        await ctx.send("Bakim modu **KAPATILDI**.")
    else:
        with _maint_lock: cur = _maintenance["active"]; msg = _maintenance["message"]
        await ctx.send(f"Bakim: **{'ACIK' if cur else 'KAPALI'}**\nMesaj: {msg}\n\n/bakim ac [mesaj] veya /bakim kapat")

@bot.command(name="mesaj")
async def mesaj_cmd(ctx, hedef_id: str = "", *, metin: str = ""):
    if not _is_admin(ctx): return
    if not hedef_id or not hedef_id.isdigit():
        await ctx.send("Kullanim: /mesaj <discord_id> <metin>"); return
    if not metin:
        await ctx.send("Mesaj metni bos olamaz."); return
    try:
        user = await bot.fetch_user(int(hedef_id))
        embed = discord.Embed(title="Hapsetme Sorgu - Mesaj", description=metin, color=0x6366f1)
        embed.set_footer(text="Hapsetme Sorgu Ekibi")
        await user.send(embed=embed)
        await ctx.send(f"Mesaj **{user.name}** kullanicisina gonderildi.")
    except discord.NotFound:
        await ctx.send(f"Kullanici bulunamadi: {hedef_id}")
    except discord.Forbidden:
        await ctx.send("Bu kullaniciya DM gonderilemiyor.")
    except Exception as e:
        await ctx.send(f"Hata: {e}")

@bot.command(name="yardim")
async def yardim_cmd(ctx):
    if not _is_admin(ctx): return
    embed = discord.Embed(title="Hapsetme Bot Komutlari", color=0x6366f1)
    embed.add_field(name="/kredi <id> <miktar>",     value="Kullaniciya kredi ver/al",           inline=False)
    embed.add_field(name="/krediherkese <miktar>",   value="Tum kullanicilara toplu kredi ver/al", inline=False)
    embed.add_field(name="/istatistik",              value="Canli site istatistigi goster",        inline=False)
    embed.add_field(name="/kredisil <id> <miktar>",  value="Kullanicidan kredi sil",              inline=False)
    embed.add_field(name="/kredigor <id>",         value="Kullanicinin kredisini gor",          inline=False)
    embed.add_field(name="/topkredi",              value="En cok kredisi olan top 10",          inline=False)
    embed.add_field(name="/bakiye",                value="Kendi kredi bakiyeni gor (herkes)",   inline=False)
    embed.add_field(name="/krediyenile",           value="Herkese +10 kredi ekle",             inline=False)
    embed.add_field(name="/kredisifirla",          value="Herkesin kredisini 0 yap",           inline=False)
    embed.add_field(name="/duyurudm <metin>",      value="Tum kullanicilara DM gonder",        inline=False)
    embed.add_field(name="/ban <id>",              value="Kara listeye al + key sil",          inline=False)
    embed.add_field(name="/unban <id>",            value="Kara listeden cikar",                inline=False)
    embed.add_field(name="/liste",                 value="Kara listedeki ID'leri goster",      inline=False)
    embed.add_field(name="/aktif",                 value="Su an sitede olan kullanicilar",      inline=False)
    embed.add_field(name="/duyuru <metin>",        value="Siteye duyuru gonder (bos = kapat)", inline=False)
    embed.add_field(name="/youtube <url>",         value="Site YT videosunu degistir",         inline=False)
    embed.add_field(name="/sure <dakika>",         value="Key gecerlilik suresini degistir",   inline=False)
    embed.add_field(name="/bekleme <saniye>",      value="YT bekleme suresini degistir",       inline=False)
    embed.add_field(name="/bakim ac|kapat",        value="Bakim modunu ac/kapat",              inline=False)
    embed.add_field(name="/mesaj <id> <metin>",    value="Kullaniciya DM gonder",              inline=False)
    embed.add_field(name="/yardim",                value="Bu mesaji goster",                   inline=False)
    embed.set_footer(text="Tum komutlar sadece DM'den calisir - Sadece admin")
    await ctx.send(embed=embed)


# ============================================================
# CALISTIR
# ============================================================
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bot.start(BOT_TOKEN))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"-- Hapsetme Sorgu Backend v2 --")
    print(f"   http://0.0.0.0:{port}")
    threading.Thread(target=run_bot, daemon=True).start()
    time.sleep(3)
    app.run(host="0.0.0.0", port=port, debug=False)


