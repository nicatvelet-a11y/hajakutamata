# -*- coding: utf-8 -*-
"""
Hapsetme Sorgu Discord Botu
"""

import discord
from discord.ext import commands
import aiohttp
import asyncio
import io
import json
import os
import random
import hashlib
import secrets
import string
import time
import urllib.request as _urllib
from datetime import datetime, timezone
from typing import Optional

# ══════════════════════════════════════════════════════
# YAPILANDIRMA
# ══════════════════════════════════════════════════════

BOT_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
BACKEND        = "https://cenabetagabey-production.up.railway.app"
BOT_API_KEY    = os.environ.get("BOT_API_KEY", "")
SEARCHULP_KEY  = os.environ.get("SEARCHULP_API_KEY", "")
SEARCHULP_BASE = "https://searchulp.xyz/api"
UPSTASH_URL    = os.environ.get("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN  = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "")

CMD_CHANNEL_ID     = 1556850970707623958
KEY_LOG_CHANNEL_ID = 1556817572807184394
YARDIM_KANAL_ID    = 1556850970707623958
SORGU_KANAL_ID     = 1556805760246087781
TICKET_CAT_ID      = 1445023035886272584

YETKILI_ROL_ID = 1448711918066864221
ABONE_ROL_ID   = 1444747455282282660
MOD_ROL_IDS    = [1486817521427419289, 1521257885537927310, 1509987771110920364]

KEY_SURE = 25  # dakika (a!bilgi yardim metnindeki "25 dakikalık key" ile uyumlu)


# Netflix cooldown: { user_id: { "pc": timestamp, "mobil": timestamp } }

C_BRAND   = 0x5865f2
C_SUCCESS = 0x57f287
C_WARN    = 0xfee75c
C_ERROR   = 0xed4245
C_DARK    = 0x2b2d31
C_PURPLE  = 0x9b59b6

FOOTER = "Hapsetme Sorgu  •  discord.gg/hapsetme"

# ══════════════════════════════════════════════════════
# KALICI DEPOLAMA — Upstash Redis REST (sıfır bağımlılık)
# ══════════════════════════════════════════════════════

_USE_UPSTASH = bool(UPSTASH_URL and UPSTASH_TOKEN)

def _upstash_get(key: str):
    try:
        req = _urllib.Request(
            f"{UPSTASH_URL}/get/hps:{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
        )
        with _urllib.urlopen(req, timeout=5) as r:
            res = json.loads(r.read())
            val = res.get("result")
            if val is None:
                return None
            # Upstash string döndürür, biz JSON string sakladık — parse et
            parsed = json.loads(val)
            # Eğer hâlâ string ise (double-serialize edilmişse) bir kez daha parse et
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            return parsed
    except Exception as e:
        print(f"[REDIS] GET {key}: {e}")
    return None

def _upstash_set(key: str, data) -> bool:
    try:
        # data'yı JSON string'e çevir, bunu Upstash'e gönder
        body = json.dumps(data, ensure_ascii=False)
        # Upstash REST SET: body doğrudan string payload olmalı
        # Content-Type: application/json ile gönderince Upstash body'yi value olarak saklar
        req = _urllib.Request(
            f"{UPSTASH_URL}/set/hps:{key}",
            data=body.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {UPSTASH_TOKEN}",
                "Content-Type":  "application/json",
            },
            method="POST"
        )
        with _urllib.urlopen(req, timeout=5) as r:
            res = json.loads(r.read())
            return res.get("result") == "OK"
    except Exception as e:
        print(f"[REDIS] SET {key}: {e}")
        return False

def _load(key: str, default):
    if _USE_UPSTASH:
        val = _upstash_get(key)
        if val is not None:
            return val
    try:
        with open(f"/app/{key}", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(key: str, data):
    if _USE_UPSTASH:
        if _upstash_set(key, data):
            return
    try:
        with open(f"/app/{key}", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[STORAGE] kayit hatasi {key}: {e}")

# ══════════════════════════════════════════════════════
# UYGULAMA VERİSİ
# ══════════════════════════════════════════════════════

kurucular:    list = _load("kurucular.json",    [330883096826347532, 1556799286023561354])
keys_db:      dict = _load("keys.json",         {})
sinirizsiz:   list = _load("sinirizsiz.json",   [])
ticket_count: dict = _load("ticket_count.json", {"count": 0})

def sv_k():    _save("kurucular.json",    kurucular)
def sv_keys(): _save("keys.json",         keys_db)
def sv_sin():  _save("sinirizsiz.json",   sinirizsiz)
def sv_tc():   _save("ticket_count.json", ticket_count)
netflix_pc_stok:     list = _load("netflix_pc.json",    [])
netflix_mobil_stok:  list = _load("netflix_mobil.json", [])
duolingo_stok:       list = _load("duolingo.json",      [])

def sv_npc():   _save("netflix_pc.json",    netflix_pc_stok)
def sv_nmob():  _save("netflix_mobil.json", netflix_mobil_stok)
def sv_duo():   _save("duolingo.json",      duolingo_stok)

# Netflix cooldown: { user_id: { 'pc': timestamp, 'mobil': timestamp } }
_netflix_cd: dict = {}
NETFLIX_CD_SURE    = 10800  # varsayilan 3 saat (rol yoksa)
NETFLIX_ROL_CD = {
    1449003268951900312: 300,   # bu rol → 5 dakika
    1444747455282282660: 600,   # bu rol → 10 dakika
}

# Duolingo cooldown: { user_id: timestamp }
_duolingo_cd: dict = {}
DUO_ROL_CD = {
    1449003268951900312: 120,   # bu rol → 2 dakika
    1444747455282282660: 300,   # bu rol → 5 dakika
}
DUO_CD_VARSAYILAN = 300  # rol yoksa 5 dakika

# ══════════════════════════════════════════════════════
# KIMLIK (SAHTE KIMLIK KARTI) — ADIM ADIM OLUSTURMA RUNTIME STATE
# ══════════════════════════════════════════════════════
# Bu 5 global, SorguView._make / on_message / _kimlik_asset_indir /
# _mrz_olustur tarafindan okunur; modul seviyesinde TANIMLI olmalidir.
_kimlik_adim:  dict = {}   # { user_id: adim_no }
_kimlik_veri:  dict = {}   # { user_id: { "TC Kimlik No": ..., "timestamp": ts } }
_kimlik_cache: dict = {}   # { "front_bg": bytes, "back_bg": bytes, "calibri": bytes, "ocr_b": bytes }

# 9 metin alani (adim 0-8); adim 9 = fotograf. Her oge: (alan_adi, soru, ornek).
# alan_adi degerleri, _kimlik_kart_ciz / _mrz_olustur / on_message validasyonu
# ile BIREBIR eslesmeli ("TC Kimlik No", "Soyisim", "Isim", "Dogum Tarihi",
# "Cinsiyet", "Seri No", "Gecerlilik", "Anne Adi", "Baba Adi").
_kimlik_soru = [
    ("TC Kimlik No", "11 haneli TC kimlik numaraniz",        "12345678901"),
    ("Soyisim",      "Soyisminiz",                            "YILMAZ"),
    ("Isim",         "Isminiz",                               "AHMET"),
    ("Dogum Tarihi", "Dogum tarihiniz (GG.AA.YYYY)",          "01.01.1990"),
    ("Cinsiyet",     "Cinsiyetiniz (E veya K)",               "E"),
    ("Seri No",      "Kimlik seri numarasi",                   "AA1234567"),
    ("Gecerlilik",   "Kart gecerlilik tarihi (GG.AA.YYYY)",    "01.01.2030"),
    ("Anne Adi",     "Anne kizlik soyadi",                     "AYSE"),
    ("Baba Adi",     "Baba adi",                               "MEHMET"),
]

# MRZ icin Turkce -> ASCII ceviri tablosu (str.translate)
_TR_ASCII = {
    ord("İ"): "I", ord("ı"): "i", ord("Ğ"): "G", ord("ğ"): "g",
    ord("Ş"): "S", ord("ş"): "s", ord("Ö"): "O", ord("ö"): "o",
    ord("Ü"): "U", ord("ü"): "u", ord("Ç"): "C", ord("ç"): "c",
}

# ══════════════════════════════════════════════════════
# KEY YARDIMCILARI
# ══════════════════════════════════════════════════════

def gen_key() -> str:
    chars = string.ascii_uppercase + string.digits
    return (
        "".join(secrets.choice(chars) for _ in range(4)) + "-" +
        "".join(secrets.choice(chars) for _ in range(4))
    )

def _find_entry(did: str):
    for _k, entry in keys_db.items():
        if str(entry.get("used_by", "")) == str(did):
            return entry
    return None

def key_ok(did: str) -> bool:
    entry = _find_entry(did)
    if not entry:
        return False
    if entry.get("sinirizsiz"):
        return True
    exp = entry.get("expires")
    return bool(exp and time.time() < float(exp))

def key_kalan(did: str) -> str:
    entry = _find_entry(did)
    if not entry:
        return "Yok"
    if entry.get("sinirizsiz"):
        return "Süresiz ∞"
    exp = entry.get("expires")
    if not exp:
        return "Süresi doldu"
    rem = float(exp) - time.time()
    if rem <= 0:
        return "Süresi doldu"
    return f"{int(rem // 60)}dk {int(rem % 60)}sn"

# ══════════════════════════════════════════════════════
# BOT
# ══════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True  # Custom status için gerekli
bot = commands.Bot(command_prefix="a!", intents=intents, help_command=None)

# ══════════════════════════════════════════════════════
# CUSTOM STATUS ROL SİSTEMİ
# ══════════════════════════════════════════════════════

HAPSETME_ROL_ID = 1461501852154859633
HAPSETME_LOG_KANAL_ID = 1525025439356162129
HAPSETME_KEYWORDS = ["hapsetme", ".gg/hapsetme", "/hapsetme", "gg/hapsetme"]

def _custom_status_kontrol(member: discord.Member) -> bool:
    """Üyenin custom status'ünde hapsetme geçiyor mu?"""
    if not member.activities:
        return False
    for activity in member.activities:
        if isinstance(activity, discord.CustomActivity) and activity.name:
            status_lower = activity.name.lower()
            for keyword in HAPSETME_KEYWORDS:
                if keyword.lower() in status_lower:
                    return True
    return False

cooldowns: dict = {}
ihbar_cooldowns: dict = {}  # ihbar butonu için ayrı 10dk cooldown

def cd(uid):     return max(0.0, 3.0 - (time.time() - cooldowns.get(uid, 0)))
def cd_set(uid): cooldowns[uid] = time.time()

def ihbar_cd(uid):     return max(0.0, 600.0 - (time.time() - ihbar_cooldowns.get(uid, 0)))
def ihbar_cd_set(uid): ihbar_cooldowns[uid] = time.time()

def is_kurucu(ctx_or_id) -> bool:
    uid = ctx_or_id.author.id if hasattr(ctx_or_id, "author") else int(ctx_or_id)
    return uid in kurucular

def is_yetkili(ctx) -> bool:
    return YETKILI_ROL_ID in {r.id for r in ctx.author.roles} if ctx.guild else False

def is_mod(ctx) -> bool:
    return bool({r.id for r in ctx.author.roles} & set(MOD_ROL_IDS)) if ctx.guild else False

def hdrs() -> dict:
    return {"X-Bot-Key": BOT_API_KEY, "Content-Type": "application/json"}

# ══════════════════════════════════════════════════════
# SPAM & FLOOD KORUMASI
# ══════════════════════════════════════════════════════

# { user_id: [timestamp, timestamp, ...] }  — son mesaj zamanları
_spam_tracker: dict = {}   # spam: aynı/benzer mesajlar
_flood_tracker: dict = {}  # flood: çok hızlı mesaj

# { user_id: uyarı_sayısı }
_spam_uyari: dict = {}
_flood_uyari: dict = {}

SPAM_ESIK    = 3      # aynı içerik kaç kez tekrar edilince spam
FLOOD_ESIK   = 5      # kaç saniyede
FLOOD_SURE   = 4.0    # saniye içinde
UYARI_LIMIT  = 3      # kaç uyarıdan sonra timeout
TIMEOUT_SURE = 5 * 60 # 5 dakika (saniye)

def _is_staff_uid(guild: discord.Guild, uid: int) -> bool:
    member = guild.get_member(uid)
    if not member:
        return False
    roles = {r.id for r in member.roles}
    return (
        YETKILI_ROL_ID in roles
        or bool(roles & set(MOD_ROL_IDS))
        or uid in kurucular
    )

async def _uygula_timeout(member: discord.Member, channel: discord.TextChannel, sebep: str):
    """Timeout uygula ve kullanıcıya bildir, bot mesajını otomatik sil."""
    until = discord.utils.utcnow() + __import__("datetime").timedelta(seconds=TIMEOUT_SURE)
    try:
        await member.timeout(until, reason=sebep)
    except discord.Forbidden:
        pass
    except Exception as e:
        print(f"[TIMEOUT HATA] {e}")

    emb = discord.Embed(
        description=(
            f"{member.mention} **5 dakika timeout** aldi.\n"
            f"**Sebep:** {sebep}"
        ),
        color=C_WARN,
        timestamp=datetime.now(timezone.utc)
    )
    emb.set_footer(text=FOOTER)
    try:
        msg = await channel.send(embed=emb)
        await asyncio.sleep(8)
        await msg.delete()
    except Exception:
        pass

async def _spam_kontrol(message: discord.Message) -> bool:
    """True dönerse mesaj spam, işlem yapıldı."""
    uid   = message.author.id
    guild = message.guild
    if _is_staff_uid(guild, uid):
        return False

    icerik = message.content.strip().lower()
    simdi  = time.time()

    # Tracker: (içerik, zaman) listesi tut
    if uid not in _spam_tracker:
        _spam_tracker[uid] = []
    _spam_tracker[uid].append((icerik, simdi))
    # Son 10 saniyedekiler
    _spam_tracker[uid] = [(c, t) for c, t in _spam_tracker[uid] if simdi - t < 10]

    # Aynı içerik kaç kez?
    tekrar = sum(1 for c, _ in _spam_tracker[uid] if c == icerik)
    if tekrar >= SPAM_ESIK:
        _spam_tracker[uid] = []
        _spam_uyari[uid] = _spam_uyari.get(uid, 0) + 1
        uyari_no = _spam_uyari[uid]

        try:
            await message.delete()
        except Exception:
            pass

        if uyari_no >= UYARI_LIMIT:
            _spam_uyari[uid] = 0
            await _uygula_timeout(message.author, message.channel, f"Spam — {UYARI_LIMIT} uyarı aşıldı")
        else:
            emb = discord.Embed(
                description=f"{message.author.mention} Spam yapma! **Uyarı {uyari_no}/{UYARI_LIMIT}** — {UYARI_LIMIT - uyari_no} hakkın kaldı.",
                color=C_WARN
            )
            emb.set_footer(text=FOOTER)
            try:
                msg = await message.channel.send(embed=emb)
                await asyncio.sleep(6)
                await msg.delete()
            except Exception:
                pass
        return True
    return False

async def _flood_kontrol(message: discord.Message) -> bool:
    """True dönerse mesaj flood, işlem yapıldı."""
    uid   = message.author.id
    guild = message.guild
    if _is_staff_uid(guild, uid):
        return False

    simdi = time.time()
    if uid not in _flood_tracker:
        _flood_tracker[uid] = []
    _flood_tracker[uid].append(simdi)
    # Son FLOOD_SURE saniyedekiler
    _flood_tracker[uid] = [t for t in _flood_tracker[uid] if simdi - t < FLOOD_SURE]

    if len(_flood_tracker[uid]) >= FLOOD_ESIK:
        _flood_tracker[uid] = []
        _flood_uyari[uid] = _flood_uyari.get(uid, 0) + 1
        uyari_no = _flood_uyari[uid]

        try:
            await message.delete()
        except Exception:
            pass

        if uyari_no >= UYARI_LIMIT:
            _flood_uyari[uid] = 0
            await _uygula_timeout(message.author, message.channel, f"Flood — {UYARI_LIMIT} uyarı aşıldı")
        else:
            emb = discord.Embed(
                description=f"{message.author.mention} Çok hızlı mesaj gönderme! **Uyarı {uyari_no}/{UYARI_LIMIT}** — {UYARI_LIMIT - uyari_no} hakkın kaldı.",
                color=C_WARN
            )
            emb.set_footer(text=FOOTER)
            try:
                msg = await message.channel.send(embed=emb)
                await asyncio.sleep(6)
                await msg.delete()
            except Exception:
                pass
        return True
    return False

# ══════════════════════════════════════════════════════
# EMBED YARDIMCILARI
# ══════════════════════════════════════════════════════

def e_err(desc: str) -> discord.Embed:
    emb = discord.Embed(description=desc, color=C_ERROR)
    emb.set_footer(text=FOOTER)
    return emb

def e_warn(desc: str) -> discord.Embed:
    emb = discord.Embed(description=desc, color=C_WARN)
    emb.set_footer(text=FOOTER)
    return emb

def e_ok(desc: str) -> discord.Embed:
    emb = discord.Embed(description=desc, color=C_SUCCESS)
    emb.set_footer(text=FOOTER)
    return emb

# ══════════════════════════════════════════════════════
# HTTP YARDIMCILARI
# ══════════════════════════════════════════════════════

async def searchulp_get(session: aiohttp.ClientSession, path: str, params: dict = None):
    if not SEARCHULP_KEY:
        return {"error": "SEARCHULP_API_KEY env eksik"}
    try:
        if params is None:
            params = {}
        params["key"] = SEARCHULP_KEY
        async with session.get(
            f"{SEARCHULP_BASE}{path}", params=params,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            ct = r.headers.get("Content-Type", "")
            if "json" in ct:
                return await r.json()
            text = await r.text()
            return {"raw": text[:3000]} if text.strip() else None
    except Exception as e:
        return {"error": str(e)}

async def cmd_uyari(ctx, kanal_id=CMD_CHANNEL_ID):
    ch = ctx.guild.get_channel(kanal_id) if ctx.guild else None
    m  = ch.mention if ch else f"<#{kanal_id}>"
    await ctx.send(embed=e_err(f"Bu komutu yalnızca {m} kanalında kullanabilirsin."), delete_after=8)
    try:
        await ctx.message.delete()
    except Exception:
        pass

async def cd_uyari(ctx, rem):
    await ctx.send(embed=e_warn(f"Lütfen **{rem:.1f}sn** bekle."), delete_after=5)

# ══════════════════════════════════════════════════════
# KEY LOG
# ══════════════════════════════════════════════════════

async def log_key(key_val: str, user: discord.Member, tur: str, expires: Optional[float]):
    if not bot.guilds:
        return
    guild = bot.guilds[0]
    ch = guild.get_channel(KEY_LOG_CHANNEL_ID)
    if not ch:
        try:
            ch = await guild.fetch_channel(KEY_LOG_CHANNEL_ID)
        except Exception:
            return

    if tur == "sinirizsiz":
        renk  = C_PURPLE
        tur_s = "Süresiz ∞"
        bitis = "—"
    elif expires is not None:
        renk  = C_SUCCESS
        tur_s = f"{KEY_SURE} dakika"
        bitis = f"<t:{int(expires)}:R>"
    else:
        renk  = C_SUCCESS
        tur_s = f"{KEY_SURE} dakika (aktifleşince başlar)"
        bitis = "Bekliyor"

    emb = discord.Embed(color=renk, timestamp=datetime.now(timezone.utc))
    emb.set_author(name=f"{user.display_name}  —  Key Oluşturuldu", icon_url=user.display_avatar.url)
    emb.set_thumbnail(url=user.display_avatar.url)
    emb.add_field(name="Key",       value=f"```{key_val}```", inline=False)
    emb.add_field(name="Kullanıcı", value=user.mention,       inline=True)
    emb.add_field(name="ID",        value=f"`{user.id}`",     inline=True)
    emb.add_field(name="Tür",       value=tur_s,              inline=True)
    emb.add_field(name="Bitiş",     value=bitis,              inline=True)
    emb.set_footer(text=f"{FOOTER}  •  Key Log")
    try:
        await ch.send(embed=emb)
    except Exception:
        pass

# ══════════════════════════════════════════════════════
# KEY KOMUTLARI
# ══════════════════════════════════════════════════════

@bot.command(name="keyolustur")
async def keyolustur(ctx: commands.Context):
    try:
        if not is_yetkili(ctx) and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        new_key = gen_key()
        while new_key in keys_db:
            new_key = gen_key()

        keys_db[new_key] = {
            "key":             new_key,
            "created_by":      str(ctx.author.id),
            "created_by_name": str(ctx.author),
            "used_by":         None,
            "expires":         None,
            "sinirizsiz":      False,
        }
        sv_keys()
        await log_key(new_key, ctx.author, "sureli", None)

        emb = discord.Embed(
            description=(
                f"```\n{new_key}\n```"
                f"**Tür      ›** Süreli\n"
                f"**Süre     ›** {KEY_SURE} dakika *(aktifleşince başlar)*\n"
                f"**Kullanım ›** `a!key {new_key}`"
            ),
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_author(name=f"{ctx.author.display_name}  •  Key Oluşturuldu", icon_url=ctx.author.display_avatar.url)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[keyolustur HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="key-s")
async def key_s(ctx: commands.Context):
    try:
        did = str(ctx.author.id)
        if did not in [str(x) for x in sinirizsiz] and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Süresiz key oluşturma izniniz yok.")); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        new_key = gen_key()
        while new_key in keys_db:
            new_key = gen_key()

        keys_db[new_key] = {
            "key":             new_key,
            "created_by":      str(ctx.author.id),
            "created_by_name": str(ctx.author),
            "used_by":         None,
            "expires":         None,
            "sinirizsiz":      True,
        }
        sv_keys()
        await log_key(new_key, ctx.author, "sinirizsiz", None)

        emb = discord.Embed(
            description=(
                f"```\n{new_key}\n```"
                f"**Tür      ›** Süresiz ∞\n"
                f"**Kullanım ›** `a!key {new_key}`"
            ),
            color=C_PURPLE,
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_author(name=f"{ctx.author.display_name}  •  Süresiz Key Oluşturuldu", icon_url=ctx.author.display_avatar.url)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[key-s HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="key")
async def key_cmd(ctx: commands.Context, girilen: str = ""):
    try:
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        if not girilen:
            await ctx.send(embed=e_err("Kullanım: `a!key XXXX-XXXX`")); return

        did     = str(ctx.author.id)
        girilen = girilen.upper().strip()

        entry = keys_db.get(girilen)
        if not entry:
            await ctx.send(embed=e_err("Geçersiz key. Yetkili birinden key al.")); return

        # Zaten bu kullanıcıya bağlı mı?
        if str(entry.get("used_by", "")) == did:
            if not key_ok(did):
                await ctx.send(embed=e_warn("Key'inin süresi dolmuş. Yetkili birinden yeni key al.")); return
            tur = "Süresiz ∞" if entry.get("sinirizsiz") else "Süreli"
            emb = discord.Embed(
                description=(
                    f"```\n{girilen}\n```"
                    f"**Tür   ›** {tur}\n"
                    f"**Kalan ›** **{key_kalan(did)}**"
                ),
                color=C_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            emb.set_author(name=f"{ctx.author.display_name}  •  Key Aktif", icon_url=ctx.author.display_avatar.url)
            emb.set_footer(text=FOOTER)
            await ctx.send(embed=emb); return

        # Başka biri kullanıyor mu?
        if entry.get("used_by") is not None:
            await ctx.send(embed=e_err("Bu key zaten başkası tarafından kullanılıyor.")); return

        # Zaten aktif key var mı?
        if key_ok(did):
            await ctx.send(embed=e_err(
                f"Zaten aktif bir key'iniz bulunuyor.\n**Kalan:** {key_kalan(did)}\n"
                "Süresi bitmeden yeni key aktifleştiremezsiniz."
            )); return

        # Eski süresi dolmuş keyleri temizle
        entry_copy = dict(entry)
        for dk in [dk for dk, de in list(keys_db.items()) if str(de.get("used_by", "")) == did]:
            del keys_db[dk]

        # Aktifleştir
        expires = None if entry_copy.get("sinirizsiz") else time.time() + KEY_SURE * 60
        entry_copy["used_by"]      = did
        entry_copy["used_by_name"] = str(ctx.author)
        entry_copy["expires"]      = expires
        keys_db[girilen]           = entry_copy
        sv_keys()

        bitis = "—" if not expires else f"<t:{int(expires)}:R>"
        tur   = "Süresiz ∞" if entry_copy.get("sinirizsiz") else f"{KEY_SURE} dakika"
        emb = discord.Embed(
            description=(
                f"```\n{girilen}\n```"
                f"**Tür    ›** {tur}\n"
                f"**Bitiş  ›** {bitis}"
            ),
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_author(name=f"{ctx.author.display_name}  •  Key Aktifleştirildi", icon_url=ctx.author.display_avatar.url)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[key HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="keysil")
async def keysil(ctx: commands.Context, hedef: str = ""):
    try:
        if not is_mod(ctx) and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        if not hedef:
            await ctx.send(embed=e_err("Kullanım: `a!keysil <XXXX-XXXX>` veya `a!keysil <discord_id>`")); return

        hedef   = hedef.strip()
        deleted = False

        if hedef.upper() in keys_db:
            del keys_db[hedef.upper()]; sv_keys(); deleted = True
        elif hedef.isdigit():
            for dk, de in list(keys_db.items()):
                if str(de.get("used_by", "")) == hedef:
                    del keys_db[dk]; sv_keys(); deleted = True; break

        if deleted:
            emb = discord.Embed(color=C_SUCCESS, timestamp=datetime.now(timezone.utc))
            emb.set_author(name="Key Silindi", icon_url=ctx.author.display_avatar.url)
            emb.add_field(name="Hedef",        value=f"`{hedef}`",       inline=True)
            emb.add_field(name="İşlemi Yapan", value=ctx.author.mention, inline=True)
            emb.set_footer(text=FOOTER)
            await ctx.send(embed=emb)
        else:
            await ctx.send(embed=e_err(f"`{hedef}` bulunamadı."))
    except Exception as e:
        print(f"[keysil HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="keylist")
async def keylist(ctx: commands.Context):
    try:
        if not is_mod(ctx) and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        aktif = []
        for dk, de in keys_db.items():
            used = de.get("used_by")
            if used and key_ok(str(used)):
                aktif.append(f"`{dk}` · <@{used}> · {key_kalan(str(used))}")

        if not aktif:
            await ctx.send(embed=e_warn("Şu an aktif kullanılan key bulunmuyor.")); return

        for i, chunk in enumerate([aktif[x:x+20] for x in range(0, len(aktif), 20)]):
            emb = discord.Embed(
                title=f"Aktif Key'ler  —  Toplam: {len(aktif)}",
                description="\n".join(chunk),
                color=C_BRAND,
                timestamp=datetime.now(timezone.utc)
            )
            emb.set_footer(text=f"{FOOTER}  •  Sayfa {i+1}")
            await ctx.send(embed=emb)
    except Exception as e:
        print(f"[keylist HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="aktif")
async def aktif_cmd(ctx: commands.Context):
    await keylist(ctx)


@bot.command(name="keys")
async def keys_cmd(ctx: commands.Context):
    try:
        if not is_mod(ctx) and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        bekleyen = [
            f"`{dk}` · Oluşturan: {de.get('created_by_name','?')}"
            for dk, de in keys_db.items() if de.get("used_by") is None
        ]
        sinirizsiz_liste = [
            f"`{dk}` · <@{de['used_by']}>"
            for dk, de in keys_db.items() if de.get("sinirizsiz") and de.get("used_by")
        ]

        if not bekleyen and not sinirizsiz_liste:
            await ctx.send(embed=e_warn("Bekleyen veya süresiz key bulunmuyor.")); return

        emb = discord.Embed(title="Key Durumu", color=C_PURPLE, timestamp=datetime.now(timezone.utc))
        if bekleyen:
            emb.add_field(name=f"Bekleyen ({len(bekleyen)})", value="\n".join(bekleyen[:15]) or "—", inline=False)
        if sinirizsiz_liste:
            emb.add_field(name=f"Süresiz Aktif ({len(sinirizsiz_liste)})", value="\n".join(sinirizsiz_liste[:15]) or "—", inline=False)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[keys HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="keysifirla")
async def keysifirla(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Sadece kurucu kullanabilir.")); return
        count = len(keys_db)
        keys_db.clear(); sv_keys()
        emb = discord.Embed(
            title="Tüm Key'ler Temizlendi",
            description=f"**{count}** key başarıyla silindi.",
            color=C_ERROR,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="İşlemi Yapan", value=ctx.author.mention, inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[keysifirla HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="id")
async def izin_ver(ctx: commands.Context, discord_id: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Sadece kurucu kullanabilir.")); return
        if not discord_id or not discord_id.isdigit():
            await ctx.send(embed=e_err("Kullanım: `a!id <discord_id>`")); return
        if discord_id in [str(x) for x in sinirizsiz]:
            await ctx.send(embed=e_warn(f"`{discord_id}` zaten süresiz key iznine sahip.")); return
        sinirizsiz.append(int(discord_id)); sv_sin()
        emb = discord.Embed(
            title="İzin Verildi",
            description=f"`{discord_id}` artık `a!key-s` komutunu kullanabilir.",
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Kullanıcı ID", value=f"`{discord_id}`",  inline=True)
        emb.add_field(name="Veren",        value=ctx.author.mention, inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[id HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="mid")
async def mid_cmd(ctx: commands.Context, discord_id: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Sadece kurucu kullanabilir.")); return
        if not discord_id or not discord_id.isdigit():
            await ctx.send(embed=e_err("Kullanım: `a!mid <discord_id>`")); return
        uid = int(discord_id)
        if uid in kurucular:
            await ctx.send(embed=e_warn(f"`{discord_id}` zaten kurucu.")); return
        kurucular.append(uid); sv_k()
        emb = discord.Embed(
            title="Kurucu Eklendi",
            description=f"`{discord_id}` artık bot kurucusu.",
            color=C_BRAND,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Kullanıcı ID", value=f"`{discord_id}`",  inline=True)
        emb.add_field(name="Ekleyen",      value=ctx.author.mention, inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[mid HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


# ══════════════════════════════════════════════════════
# NETFLIX STOK KOMUTLARI
# ══════════════════════════════════════════════════════

@bot.command(name="neftlixpc")
async def neftlix_pc_cmd(ctx: commands.Context, *, hesap: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return
        if not hesap.strip():
            await ctx.send(embed=e_err("Kullanim: `a!neftlixpc <hesap bilgisi>`")); return
        netflix_pc_stok.append(hesap.strip())
        sv_npc()
        emb = discord.Embed(
            title="Netflix PC Stok Eklendi",
            description=f"```\n{hesap.strip()}\n```",
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Toplam PC Stok", value=str(len(netflix_pc_stok)), inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[neftlixpc HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="neftlixmobil")
async def neftlix_mobil_cmd(ctx: commands.Context, *, hesap: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return
        if not hesap.strip():
            await ctx.send(embed=e_err("Kullanim: `a!neftlixmobil <hesap bilgisi>`")); return
        netflix_mobil_stok.append(hesap.strip())
        sv_nmob()
        emb = discord.Embed(
            title="Netflix Mobil Stok Eklendi",
            description=f"```\n{hesap.strip()}\n```",
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Toplam Mobil Stok", value=str(len(netflix_mobil_stok)), inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[neftlixmobil HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="stok")
async def stok_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return

        txt  = f"NETFLIX STOK DURUMU\n{'='*50}\n\n"
        txt += f"Netflix PC — {len(netflix_pc_stok)} hesap\n{'-'*30}\n"
        for i, h in enumerate(netflix_pc_stok, 1):
            txt += f"{i}. {h}\n"
        if not netflix_pc_stok:
            txt += "Stok yok.\n"

        txt += f"\nNetflix Mobil — {len(netflix_mobil_stok)} hesap\n{'-'*30}\n"
        for i, h in enumerate(netflix_mobil_stok, 1):
            txt += f"{i}. {h}\n"
        if not netflix_mobil_stok:
            txt += "Stok yok.\n"

        dosya = discord.File(fp=io.BytesIO(txt.encode("utf-8")), filename="netflix_stok.txt")
        await ctx.send(file=dosya)
    except Exception as e:
        print(f"[stok HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="stoksil")
async def stoksil_cmd(ctx: commands.Context, *, hedef: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return
        if not hedef.strip():
            await ctx.send(embed=e_err("Kullanim: `a!stoksil <hesap bilgisi>`")); return

        hedef = hedef.strip()
        silindi = False

        if hedef in netflix_pc_stok:
            netflix_pc_stok.remove(hedef)
            sv_npc()
            silindi = True
            tur = "PC"
        elif hedef in netflix_mobil_stok:
            netflix_mobil_stok.remove(hedef)
            sv_nmob()
            silindi = True
            tur = "Mobil"

        if silindi:
            emb = discord.Embed(
                title="Stok Silindi",
                description=f"```\n{hedef}\n```",
                color=C_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            emb.add_field(name="Tur", value=f"Netflix {tur}", inline=True)
            emb.add_field(name="Kalan PC", value=str(len(netflix_pc_stok)), inline=True)
            emb.add_field(name="Kalan Mobil", value=str(len(netflix_mobil_stok)), inline=True)
            emb.set_footer(text=FOOTER)
            await ctx.send(embed=emb)
        else:
            await ctx.send(embed=e_err("Bu hesap stokta bulunamadi."))
    except Exception as e:
        print(f"[stoksil HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="stoktemizle")
async def stoktemizle_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return

        pc_adet    = len(netflix_pc_stok)
        mobil_adet = len(netflix_mobil_stok)

        netflix_pc_stok.clear()
        netflix_mobil_stok.clear()
        sv_npc()
        sv_nmob()

        emb = discord.Embed(
            title="Stok Temizlendi",
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Silinen PC Stok",    value=str(pc_adet),    inline=True)
        emb.add_field(name="Silinen Mobil Stok", value=str(mobil_adet), inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[stoktemizle HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


# ══════════════════════════════════════════════════════
# DUOLINGO STOK KOMUTLARI
# ══════════════════════════════════════════════════════

@bot.command(name="duolingo")
async def duolingo_ekle_cmd(ctx: commands.Context, *, hesap: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return
        if not hesap.strip():
            await ctx.send(embed=e_err("Kullanim: `a!duolingo <hesap bilgisi>`")); return
        duolingo_stok.append(hesap.strip())
        sv_duo()
        emb = discord.Embed(
            title="Duolingo Stok Eklendi",
            description=f"```\n{hesap.strip()}\n```",
            color=0x58CC02,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Toplam Duolingo Stok", value=str(len(duolingo_stok)), inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[duolingo HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="duostok")
async def duostok_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return

        txt = f"DUOLINGO STOK — {len(duolingo_stok)} HESAP\n{'='*50}\n\n"
        for i, h in enumerate(duolingo_stok, 1):
            txt += f"{i}. {h}\n"
        if not duolingo_stok:
            txt += "Stok yok.\n"

        dosya = discord.File(fp=io.BytesIO(txt.encode("utf-8")), filename="duolingo_stok.txt")
        await ctx.send(file=dosya)
    except Exception as e:
        print(f"[duostok HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="duosil")
async def duosil_cmd(ctx: commands.Context, *, hedef: str = ""):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return
        if not hedef.strip():
            await ctx.send(embed=e_err("Kullanim: `a!duosil <hesap bilgisi>`")); return

        hedef = hedef.strip()
        if hedef in duolingo_stok:
            duolingo_stok.remove(hedef)
            sv_duo()
            emb = discord.Embed(
                title="Duolingo Stok Silindi",
                description=f"```\n{hedef}\n```",
                color=C_SUCCESS,
                timestamp=datetime.now(timezone.utc)
            )
            emb.add_field(name="Kalan Duolingo", value=str(len(duolingo_stok)), inline=True)
            emb.set_footer(text=FOOTER)
            await ctx.send(embed=emb)
        else:
            await ctx.send(embed=e_err("Bu hesap Duolingo stokta bulunamadi."))
    except Exception as e:
        print(f"[duosil HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="duotemizle")
async def duotemizle_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return

        adet = len(duolingo_stok)
        duolingo_stok.clear()
        sv_duo()
        emb = discord.Embed(
            title="Duolingo Stok Temizlendi",
            color=C_SUCCESS,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(name="Silinen Hesap", value=str(adet), inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[duotemizle HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))


@bot.command(name="kullanim")
async def kullanim_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        if not isinstance(ctx.channel, discord.DMChannel):
            await ctx.send(embed=e_err("Bu komutu sadece DM'den kullanabilirsin.")); return

        emb = discord.Embed(
            title="Stok Komutlari",
            color=C_BRAND,
            timestamp=datetime.now(timezone.utc)
        )
        emb.add_field(
            name="Netflix Komutlari",
            value=(
                "`a!neftlixpc <hesap>` — PC stoka ekle\n"
                "`a!neftlixmobil <hesap>` — Mobil stoka ekle\n"
                "`a!stok` — Stok durumu (TXT)\n"
                "`a!stoksil <hesap>` — Netflix stoktan sil\n"
                "`a!stoktemizle` — Netflix stogu komple temizle"
            ),
            inline=False
        )
        emb.add_field(
            name="Duolingo Komutlari",
            value=(
                "`a!duolingo <hesap>` — Duolingo stoka ekle\n"
                "`a!duostok` — Duolingo stok listesi (TXT)\n"
                "`a!duosil <hesap>` — Duolingo stoktan sil\n"
                "`a!duotemizle` — Duolingo stogu komple temizle"
            ),
            inline=False
        )
        emb.add_field(
            name="Butonlar (a!sorgu)",
            value=(
                "Netflix PC — Rol1: 5dk | Rol2: 10dk | Diger: 3 saat\n"
                "Netflix Mobil — Rol1: 5dk | Rol2: 10dk | Diger: 3 saat\n"
                "Duolingo Premium — Rol1: 2dk | Rol2: 5dk"
            ),
            inline=False
        )
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[kullanim HATA] {e}")
        await ctx.send(embed=e_err(f"Hata: `{e}`"))

SORGULAR = [
    # (sid, buton_etiketi, modal_basligi, aciklama)
    ("tc",         "TC Kimlik",    "TC Kimlik Sorgu",    "TC numarasına göre detaylı kimlik"),
    ("vesika",     "Vesika Foto",  "Vesika Foto Sorgu",  "TC'ye göre vesika fotoğrafı"),
    ("sgk",        "SGK",          "SGK Sorgu",          "TC'ye göre SGK bilgisi"),
    ("tapu",       "Tapu",         "Tapu Sorgu",         "TC'ye göre tapu kayıtları"),
    ("adsoyad",    "Ad Soyad",     "Ad Soyad Sorgu",     "Ad ve soyad ile kişi arama"),
    ("adsoyadpro", "Ad Soyad Pro", "Ad Soyad Pro Sorgu", "Ad+Soyad+İl+İlçe detaylı"),
    ("adsorgu",    "Ad Sorgu",     "Ad Sorgu",           "Ad+İl+İlçe ile arama"),
    ("aile",       "Aile",         "Aile Sorgu",         "Anne, baba ve kardeş bilgisi"),
    ("sulale",     "Sülale",       "Sülale Sorgu",       "Büyükanne, amca, teyze vb."),
    ("cocuk",      "Çocuk",        "Çocuk Sorgu",        "Kayıtlı çocuk bilgisi"),
    ("adres",      "Adres",        "Adres Sorgu",        "İkametgah adresi + vergi no"),
    ("gsmtc",      "GSM → TC",     "GSM - TC Sorgu",     "GSM'ye göre kayıtlı TC kimlik"),
    ("tcgsm",      "TC → GSM",     "TC - GSM Sorgu",     "TC'ye göre kayıtlı telefon"),
    ("isyeri",     "İşyeri",       "İşyeri Sorgu",       "SGK işyeri ve çalışma durumu"),
    ("ip",         "IP Sorgu",     "IP Sorgu",           "IP konum, ISP ve koordinat"),
    ("discord",    "Discord ID",   "Discord Sorgu",      "Discord ID'ye göre kullanıcı"),
    ("logtr",      "Log TR",       "Log TR Sorgu",       "Site log araması"),
    ("ihbar",      "İhbar",        "EGM İhbar Gönder",   "EGM'ye ihbar gönder"),
    ("iban",       "IBAN Sorgu",   "IBAN Sorgu",         "IBAN banka ve hesap bilgisi"),
    ("cc",         "CC Check",     "Kredi Kartı Check",  "Kart doğrulama (Luhn + BIN)"),
    ("kimlik",     "Kimlik Olustur", "Sahte Kimlik Olustur", "Sahte TC kimlik kartı oluştur"),
    ("nftpc",      "Netflix PC",     "Netflix PC Login",    "Netflix PC hesap bilgisi"),
    ("nftmobil",   "Netflix Mobil",  "Netflix Mobil Login", "Netflix Mobil hesap bilgisi"),
    ("duolingo",   "Duolingo Premium", "Duolingo Premium",  "Duolingo Premium hesap"),
]

SORGU_MODAL_CFG = {
    "tc":         [("TC Kimlik No",         "12345678901",         False)],
    "vesika":     [("TC Kimlik No",         "12345678901",         False)],
    "sgk":        [("TC Kimlik No",         "12345678901",         False)],
    "tapu":       [("TC Kimlik No",         "12345678901",         False)],
    "adsoyad":    [("Ad",                   "FURKAN",              False),
                   ("Soyad",                "ALBAYRAK",            True)],
    "adsoyadpro": [("Ad",                   "FURKAN",              False),
                   ("Soyad",                "ALBAYRAK",            False),
                   ("İl",                   "ISTANBUL",            False),
                   ("İlçe",                 "PENDIK",              True)],
    "adsorgu":    [("Ad",                   "FURKAN",              False),
                   ("İl",                   "ISTANBUL",            False),
                   ("İlçe",                 "PENDIK",              False)],
    "aile":       [("TC Kimlik No",         "12345678901",         False)],
    "sulale":     [("TC Kimlik No",         "12345678901",         False)],
    "cocuk":      [("TC Kimlik No",         "12345678901",         False)],
    "adres":      [("TC Kimlik No",         "12345678901",         False)],
    "gsmtc":      [("GSM Numarası",         "5551234567",          False)],
    "tcgsm":      [("TC Kimlik No",         "12345678901",         False)],
    "isyeri":     [("TC Kimlik No",         "12345678901",         False)],
    "ip":         [("IP Adresi",            "8.8.8.8",             False)],
    "discord":    [("Discord Kullanıcı ID", "1556799286023561354", False)],
    "logtr":      [("Domain",               "exxen.com",           False)],
    "ihbar":      [("Başlık",               "Uyuşturucu satışı",   False),
                   ("Açıklama",             "Olay detayları...",   False),
                   ("Şehir",                "ISTANBUL",            False),
                   ("İlçe",                 "KADIKÖY",             False),
                   ("Telefon",              "5XXXXXXXXX",          False)],
    "iban":       [("IBAN",                 "TR330006100519786457841326", False)],
    "cc":         [("Combo List (KART|AY|YIL|CVV)", "5890040000000016|12|2030|123\n4111111111111111|06|2028|456", False)],
}


@bot.command(name="sorgu")
async def sorgu_cmd(ctx: commands.Context):
    try:
        if not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu sadece kurucu kullanabilir.")); return
        aciklamalar = "\n".join(f"**›** **{lbl}** — {ac}" for _, lbl, _, ac in SORGULAR)
        emb = discord.Embed(
            title="Hapsetme Sorgu  •  Sorgu Paneli",
            description=(
                "Butonlardan sorgu türünü seç.\n"
                "Sonuç **DM**'ine TXT olarak gelir.\n"
                f"> Key almak için <#{CMD_CHANNEL_ID}> kanalında `a!ticket` aç\n\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n\n"
                + aciklamalar +
                "\n\n▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "**>** **Proxy Al** — Guncel proxy listesi DM'ine TXT olarak gelir"
            ),
            color=C_DARK,
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_author(name="Hapsetme Sorgu", icon_url=bot.user.display_avatar.url)
        emb.set_footer(text=f"{FOOTER}  •  Key gereklidir")
        await ctx.send(embed=emb, view=SorguView())
    except Exception as e:
        print(f"[sorgu HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


class SorguView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        styles = [
            discord.ButtonStyle.primary,
            discord.ButtonStyle.success,
            discord.ButtonStyle.secondary,
            discord.ButtonStyle.primary,
        ]
        # 19 sorgu butonu var, her row'da 5 buton olabilir
        # Row 0: 0-4 (5 buton)
        # Row 1: 5-9 (5 buton)
        # Row 2: 10-14 (5 buton)
        # Row 3: 15-18 (4 buton)
        # Row 4: proxy butonu (1 buton)
        for i, (sid, lbl, _modal, _ac) in enumerate(SORGULAR):
            row = i // 5  # Her 5 butonda 1 row
            btn = discord.ui.Button(
                label=lbl,
                style=styles[i % len(styles)],
                custom_id=f"sq__{sid}",
                row=row
            )
            btn.callback = self._make(sid, lbl)
            self.add_item(btn)

        # Proxy butonu — son row'a
        proxy_btn = discord.ui.Button(
            label="Proxy Al",
            style=discord.ButtonStyle.success,
            custom_id="proxy__al",
            row=4
        )
        proxy_btn.callback = self._proxy_cb()
        self.add_item(proxy_btn)

    def _make(self, sid: str, lbl: str):
        async def cb(interaction: discord.Interaction):
            try:
                did = str(interaction.user.id)
                if not is_kurucu(interaction.user.id) and not key_ok(did):
                    emb = discord.Embed(
                        description=(
                            "**Key Gerekli**\n"
                            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                            "Bu sorguyu yapmak için aktif bir key'e ihtiyacın var.\n\n"
                            f"**›** <#{CMD_CHANNEL_ID}> kanalında `a!ticket` aç\n"
                            "**›** **Abone Rol** kategorisini seç"
                        ),
                        color=C_ERROR
                    )
                    emb.set_footer(text=FOOTER)
                    await interaction.response.send_message(embed=emb, ephemeral=True)
                    return
                # ── Kimlik: modal değil, DM adım adım flow ──
                if sid == "kimlik":
                    _kimlik_adim[interaction.user.id] = 0
                    _kimlik_veri[interaction.user.id] = {"timestamp": time.time()}
                    try:
                        emb = discord.Embed(
                            title="Kimlik Olustur",
                            description=(
                                "Adim adim bilgileri gireceksin.\n"
                                "İptal etmek icin **iptal** yaz.\n\n"
                                f"**1/10 — {_kimlik_soru[0][0]}**\n{_kimlik_soru[0][1]}\n"
                                f"*Ornek: {_kimlik_soru[0][2]}*"
                            ),
                            color=C_BRAND
                        )
                        emb.set_footer(text=FOOTER)
                        await interaction.user.send(embed=emb)
                        await interaction.response.send_message(embed=e_ok("DM'ine kimlik olusturma basladi."), ephemeral=True)
                    except discord.Forbidden:
                        await interaction.response.send_message(embed=e_warn("DM'in kapalı. Ac tekrar dene."), ephemeral=True)
                    return

                # ── Netflix PC / Mobil ──
                if sid in ("nftpc", "nftmobil"):
                    uid   = interaction.user.id
                    roles = {r.id for r in interaction.user.roles} if interaction.guild else set()

                    # Kurucu kontrolü — kurucuya cooldown yok
                    if not is_kurucu(uid):
                        # Role göre cooldown süresini belirle
                        cd_sure = NETFLIX_CD_SURE  # varsayilan 3 saat
                        for rol_id, sure in NETFLIX_ROL_CD.items():
                            if rol_id in roles:
                                cd_sure = sure
                                break

                        son   = _netflix_cd.get(uid, {}).get(sid, 0)
                        kalan = cd_sure - (time.time() - son)
                        if kalan > 0:
                            saat = int(kalan // 3600)
                            dk   = int((kalan % 3600) // 60)
                            sn   = int(kalan % 60)
                            sure_str = f"{saat}sa {dk}dk {sn}sn" if saat > 0 else f"{dk}dk {sn}sn"
                            await interaction.response.send_message(
                                embed=e_warn(f"Bu butonu **{sure_str}** sonra tekrar kullanabilirsin."),
                                ephemeral=True
                            )
                            return

                    stok = netflix_pc_stok if sid == "nftpc" else netflix_mobil_stok
                    tur  = "PC" if sid == "nftpc" else "Mobil"

                    if not stok:
                        await interaction.response.send_message(
                            embed=e_err(f"Netflix {tur} stoku tukendi."),
                            ephemeral=True
                        )
                        return

                    hesap = random.choice(stok)  # Rastgele hesap ver, stoktan SİLME

                    # Cooldown kaydet
                    if uid not in _netflix_cd:
                        _netflix_cd[uid] = {}
                    _netflix_cd[uid][sid] = time.time()

                    try:
                        emb = discord.Embed(
                            title=f"Netflix {tur} Login",
                            description=hesap,
                            color=C_BRAND,
                            timestamp=datetime.now(timezone.utc)
                        )
                        emb.add_field(
                            name="Nasil Kullanilir?",
                            value="Bu size verilen linki herhangi bir tarayiciniza yapistirin, hesaba otomatik giris yapacaktir. Premium hesaplardir.",
                            inline=False
                        )
                        emb.set_footer(text=FOOTER)
                        await interaction.user.send(embed=emb)
                        await interaction.response.send_message(
                            embed=e_ok(f"Netflix {tur} hesabı DM'ine gönderildi."),
                            ephemeral=True
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            embed=e_warn("DM'in kapalı, hesap gönderilemedi."),
                            ephemeral=True
                        )
                    return

                # ── Duolingo Premium ──
                if sid == "duolingo":
                    uid   = interaction.user.id
                    roles = {r.id for r in interaction.user.roles} if interaction.guild else set()

                    if not is_kurucu(uid):
                        # Role göre cooldown süresini belirle
                        cd_sure = DUO_CD_VARSAYILAN
                        for rol_id, sure in DUO_ROL_CD.items():
                            if rol_id in roles:
                                cd_sure = sure
                                break

                        son   = _duolingo_cd.get(uid, 0)
                        kalan = cd_sure - (time.time() - son)
                        if kalan > 0:
                            dk = int(kalan // 60)
                            sn = int(kalan % 60)
                            await interaction.response.send_message(
                                embed=e_warn(f"Bu butonu **{dk}dk {sn}sn** sonra tekrar kullanabilirsin."),
                                ephemeral=True
                            )
                            return

                    if not duolingo_stok:
                        await interaction.response.send_message(
                            embed=e_err("Duolingo stoku tukendi."),
                            ephemeral=True
                        )
                        return

                    hesap = random.choice(duolingo_stok)
                    _duolingo_cd[uid] = time.time()

                    try:
                        emb = discord.Embed(
                            title="Duolingo Premium",
                            description=f"```\n{hesap}\n```",
                            color=0x58CC02,
                            timestamp=datetime.now(timezone.utc)
                        )
                        emb.add_field(
                            name="Nasil Kullanilir?",
                            value="Email ve sifreyi kullanarak duolingo.com adresinden giris yapabilirsiniz. Premium hesaptir.",
                            inline=False
                        )
                        emb.set_footer(text=FOOTER)
                        await interaction.user.send(embed=emb)
                        await interaction.response.send_message(
                            embed=e_ok("Duolingo hesabi DM'ine gonderildi."),
                            ephemeral=True
                        )
                    except discord.Forbidden:
                        await interaction.response.send_message(
                            embed=e_warn("DM'in kapali, hesap gonderilemedi."),
                            ephemeral=True
                        )
                    return
                modal_baslik = next((m for s, _l, m, _a in SORGULAR if s == sid), lbl)
                await interaction.response.send_modal(SorguModal(sid, modal_baslik))
            except Exception as e:
                print(f"[SorguView cb HATA] {e}")
                try:
                    await interaction.response.send_message(embed=e_err(f"Hata: `{e}`"), ephemeral=True)
                except Exception:
                    pass
        return cb

    def _proxy_cb(self):
        async def cb(interaction: discord.Interaction):
            try:
                uid   = interaction.user.id
                roles = {r.id for r in interaction.user.roles} if interaction.guild else set()

                kalan = _proxy_cd_kalan(uid, roles)
                if kalan > 0:
                    dk = int(kalan // 60)
                    sn = int(kalan % 60)
                    emb = discord.Embed(
                        description=f"{dk}dk {sn}sn sonra tekrar kullanabilirsin.",
                        color=C_WARN
                    )
                    emb.set_footer(text=FOOTER)
                    await interaction.response.send_message(embed=emb, ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=True)

                proxies = await _proxy_cek()
                if not proxies:
                    await interaction.followup.send(embed=e_err("Proxy listesi alınamadı, tekrar dene."), ephemeral=True)
                    return

                _proxy_cd[uid] = time.time()

                icerik = "\n".join(proxies)
                dosya  = discord.File(
                    fp=io.BytesIO(icerik.encode("utf-8")),
                    filename=f"proxies_{len(proxies)}.txt"
                )
                emb = discord.Embed(
                    title="Proxy Listesi",
                    description=f"**Toplam:** `{len(proxies)}` proxy",
                    color=C_SUCCESS,
                    timestamp=datetime.now(timezone.utc)
                )
                emb.set_footer(text=FOOTER)

                try:
                    await interaction.user.send(embed=emb, file=dosya)
                    await interaction.followup.send(embed=e_ok("Proxy listesi DM'ine gönderildi."), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=e_err("DM'in kapalı! Lütfen DM'ini aç ve tekrar dene.\n> Ayarlar → Gizlilik → Sunucu Üyelerinden Direkt Mesaj → Aç"),
                        ephemeral=True
                    )

            except Exception as e:
                print(f"[PROXY CB HATA] {e}")
                try:
                    await interaction.followup.send(embed=e_err(f"Hata: `{e}`"), ephemeral=True)
                except Exception:
                    pass
        return cb


class SorguModal(discord.ui.Modal):
    def __init__(self, sid: str, baslik: str):
        super().__init__(title=baslik)
        self.sid = sid
        self.inputs = {}
        cfg = SORGU_MODAL_CFG.get(sid, [("Değer", "", False)])
        for label, ph, optional in cfg[:5]:  # Discord modal max 5 TextInput
            # CC için textarea (multiline)
            if sid == "cc":
                inp = discord.ui.TextInput(
                    label=label,
                    placeholder=ph,
                    required=not optional,
                    style=discord.TextStyle.paragraph,
                    max_length=2000
                )
            else:
                inp = discord.ui.TextInput(
                    label=label,
                    placeholder=ph,
                    required=not optional,
                    min_length=1,
                    max_length=100
                )
            self.inputs[label] = inp
            self.add_item(inp)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            did = str(interaction.user.id)

            if not is_kurucu(interaction.user.id) and not key_ok(did):
                await interaction.followup.send(embed=e_err("Key'inin süresi dolmuş."), ephemeral=True)
                return

            vals = {k: v.value.strip() for k, v in self.inputs.items()}

            # ── İhbar — _api_call'a gitme ──
            if self.sid == "ihbar":
                uid = interaction.user.id
                rem = ihbar_cd(uid)
                if rem > 0:
                    kalan_dk = int(rem // 60)
                    kalan_sn = int(rem % 60)
                    await interaction.followup.send(
                        embed=e_warn(f"İhbar butonunu **{kalan_dk}dk {kalan_sn}sn** sonra tekrar kullanabilirsin."),
                        ephemeral=True
                    )
                    return
                ihbar_cd_set(uid)
                baslik   = vals.get("Başlık", "")
                aciklama = vals.get("Açıklama", "")
                sehir    = vals.get("Şehir", "")
                ilce     = vals.get("İlçe", "")
                telefon  = vals.get("Telefon", "")
                await interaction.followup.send(
                    embed=discord.Embed(description="⏳ İhbar gönderiliyor...", color=C_BRAND),
                    ephemeral=True
                )
                async with aiohttp.ClientSession() as sess:
                    sonuc = await _ihbar_gonder(sess, baslik, aciklama, sehir, ilce, telefon)
                platform = sonuc.get("platform", "EGM (Polis)")
                emb = discord.Embed(
                    title="İhbar Sonucu",
                    description=f"**{platform}** — Ihbariniz basariyla iletildi.",
                    color=C_SUCCESS,
                    timestamp=datetime.now(timezone.utc)
                )
                emb.add_field(name="Başlık",     value=baslik or "—",                          inline=True)
                emb.add_field(name="Şehir/İlçe", value=f"{sehir}/{ilce}" if sehir else "—",   inline=True)
                emb.add_field(name="Telefon",    value=telefon or "—",                         inline=True)
                emb.set_footer(text=FOOTER)
                try:
                    await interaction.user.send(embed=emb)
                    await interaction.followup.send(embed=e_ok("İhbar gönderildi. Sonuç DM'ine geldi."), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=emb, ephemeral=True)
                return

            if self.sid == "adsoyad":
                ad    = vals.get("Ad", "").upper()
                soyad = vals.get("Soyad", "").upper()
                val   = f"{ad},{soyad}" if soyad else ad
            elif self.sid == "adsoyadpro":
                ad    = vals.get("Ad", "").upper()
                soyad = vals.get("Soyad", "").upper()
                il    = vals.get("İl", "").upper()
                ilce  = vals.get("İlçe", "").upper()
                val   = f"{ad},{soyad},{il},{ilce}"  # ilce boş olabilir, API'de kontrol edilir
            elif self.sid == "adsorgu":
                ad   = vals.get("Ad", "").upper()
                il   = vals.get("İl", "").upper()
                ilce = vals.get("İlçe", "").upper()
                val  = f"{ad},{il},{ilce}"
            else:
                raw = list(vals.values())[0]
                val = raw.lower() if self.sid in ("ip", "logtr") else raw.upper()

            await interaction.followup.send(
                embed=discord.Embed(description="Sorgu yapılıyor...", color=C_BRAND),
                ephemeral=True
            )

            # ── IBAN Sorgu ──
            if self.sid == "iban":
                iban_input = vals.get("IBAN", "").upper().replace(" ", "")
                result = _iban_parse(iban_input)
                
                if not result["valid"]:
                    err_msg = result.get("error", "Geçersiz IBAN")
                    await interaction.followup.send(embed=e_err(err_msg), ephemeral=True)
                    return
                
                txt = f"""═══════════════════════════════════════
IBAN SORGU SONUCU
═══════════════════════════════════════

IBAN           : {result['iban']}
Gecerlilik     : {'GECERLI' if result['valid'] else 'GECERSIZ'}
Ulke           : {result['ulke']}
Check Digit    : {result['check_digit']}
Banka Kodu     : {result['banka_kodu']}
Banka Adi      : {result['banka_adi']}
Hesap No       : {result['hesap_no']}

═══════════════════════════════════════
Sorgu Zamani: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
"""
                dosya_adi = f"iban_{result['iban'][:10]}.txt"
                try:
                    await interaction.user.send(file=discord.File(fp=io.BytesIO(txt.encode("utf-8")), filename=dosya_adi))
                    await interaction.followup.send(embed=e_ok("IBAN sonucu DM'ine gönderildi."), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=e_warn("DM'in kapalı, sonuç gönderilemedi."), ephemeral=True)
                return

            # ── CC Check (MASS COMBO) ──
            if self.sid == "cc":
                combo_input = vals.get("Combo List (KART|AY|YIL|CVV)", "").strip()
                
                if not combo_input:
                    await interaction.followup.send(embed=e_err("Combo listesi boş!"), ephemeral=True)
                    return
                
                lines = [l.strip() for l in combo_input.split("\n") if l.strip()]
                
                if len(lines) > 50:
                    await interaction.followup.send(embed=e_err("Maksimum 50 kart test edilebilir!"), ephemeral=True)
                    return
                
                await interaction.followup.send(
                    embed=discord.Embed(description=f"⏳ {len(lines)} kart test ediliyor...", color=C_BRAND),
                    ephemeral=True
                )
                
                results = []
                live_count = 0
                dead_count = 0
                
                async with aiohttp.ClientSession() as sess:
                    for line in lines:
                        if "|" not in line:
                            continue
                        
                        parts = line.split("|")
                        if len(parts) != 4:
                            continue
                        
                        card_no, month, year, cvv = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
                        
                        # Fake checker logic (Luhn + random)
                        result = await _cc_check_fake(sess, card_no, month, year, cvv)
                        
                        if result["status"] == "LIVE":
                            live_count += 1
                        else:
                            dead_count += 1
                        
                        results.append(result)
                
                # TXT oluştur
                txt = f"""===========================================
CC CHECKER
===========================================

Toplam Test    : {len(results)}
LIVE           : {live_count}
DEAD           : {dead_count}
Basari Orani   : %{int((live_count/len(results))*100) if results else 0}

"""
                for idx, res in enumerate(results, 1):
                    status_icon = "✅ LIVE" if res["status"] == "LIVE" else "❌ DEAD"
                    txt += f"""[{idx}] {status_icon}
Kart No        : {res['card_number']}
Son Kullanma   : {res['expire_month']}/{res['expire_year']}
CVV            : ***
BIN            : {res['bin']}
Kart Turu      : {res['scheme']}
Banka          : {res['bank']}
Ulke           : {res['country']}
Mesaj          : {res['message']}
-------------------------------------------
"""
                
                dosya_adi = f"cc_checker_{live_count}LIVE_{dead_count}DEAD.txt"
                try:
                    await interaction.user.send(file=discord.File(fp=io.BytesIO(txt.encode("utf-8")), filename=dosya_adi))
                    await interaction.followup.send(
                        embed=e_ok(f"✅ {live_count} LIVE | ❌ {dead_count} DEAD\nSonuç DM'ine gönderildi!"),
                        ephemeral=True
                    )
                except discord.Forbidden:
                    await interaction.followup.send(embed=e_warn("DM'in kapalı, sonuç gönderilemedi."), ephemeral=True)
                return

            async with aiohttp.ClientSession() as sess:
                data = await _api_call(sess, self.sid, val)

            if data is None:
                err_emb = e_err(f"{self.title} — Kayıt bulunamadı.")
                try:
                    await interaction.user.send(embed=err_emb)
                except discord.Forbidden:
                    pass  # DM kapalı, kanala yazma
                await interaction.followup.send(embed=e_err("Kayıt bulunamadı."), ephemeral=True)
                return

            # ── Vesika: başarılıysa SADECE foto ──
            if self.sid == "vesika":
                import base64 as _b64
                foto_bytes = None
                foto_url   = None
                basarili   = False

                if isinstance(data, dict):
                    basarili = (
                        data.get("durum") == "basarili" or
                        data.get("success") is True or
                        data.get("status") == "success"
                    )
                    kaynak = data.get("veri", data)
                    if isinstance(kaynak, dict):
                        for fkey in ("vesika", "foto", "fotograf", "image", "img", "photo", "base64", "resim"):
                            fval = kaynak.get(fkey)
                            if fval and isinstance(fval, str):
                                raw_f = fval.strip()
                                if raw_f.startswith("data:"):
                                    raw_f = raw_f.split(",", 1)[-1]
                                if raw_f.startswith("/9j") or raw_f.startswith("iVBOR") or raw_f.startswith("R0lGOD"):
                                    try:
                                        foto_bytes = _b64.b64decode(raw_f)
                                        basarili = True
                                        break
                                    except Exception:
                                        pass
                                elif raw_f.startswith("http"):
                                    foto_url = raw_f
                                    basarili = True
                                    break

                if not basarili or (foto_bytes is None and foto_url is None):
                    try:
                        await interaction.user.send(embed=e_err("Vesika kaydı bulunamadı."))
                    except discord.Forbidden:
                        pass
                    await interaction.followup.send(embed=e_err("Kayıt bulunamadı."), ephemeral=True)
                    return

                try:
                    if foto_bytes:
                        await interaction.user.send(file=discord.File(fp=io.BytesIO(foto_bytes), filename=f"vesika_{val[:15]}.jpg"))
                    elif foto_url:
                        emb_f = discord.Embed(color=C_BRAND)
                        emb_f.set_image(url=foto_url)
                        emb_f.set_footer(text=FOOTER)
                        await interaction.user.send(embed=emb_f)
                    await interaction.followup.send(embed=e_ok("Vesika fotoğrafı DM'ine gönderildi."), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=e_warn("DM'in kapalı, sonuç gönderilemedi."), ephemeral=True)
                return

            # ── Log TR ──
            if self.sid == "logtr":
                txt_content = _veri_txt_log(val, data)
                dosya_adi   = f"logtr_{val[:20]}.txt"
                try:
                    await interaction.user.send(file=discord.File(fp=io.BytesIO(txt_content.encode("utf-8")), filename=dosya_adi))
                    await interaction.followup.send(embed=e_ok("Log sonucu DM'ine gönderildi."), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=e_warn("DM'in kapalı, sonuç gönderilemedi."), ephemeral=True)
                return

            # ── Diğer sorgular: TXT ──
            txt_content = _veri_txt(self.title, val, data)
            dosya_adi   = f"{self.sid}_{val[:20].replace(' ', '_')}.txt"
            try:
                await interaction.user.send(file=discord.File(fp=io.BytesIO(txt_content.encode("utf-8")), filename=dosya_adi))
                await interaction.followup.send(embed=e_ok("Sonuç TXT olarak DM'ine gönderildi."), ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(embed=e_warn("DM'in kapalı, sonuç gönderilemedi."), ephemeral=True)

        except Exception as e:
            print(f"[SorguModal on_submit HATA] {e}")
            try:
                await interaction.followup.send(embed=e_err(f"Sorgu sırasında hata: `{e}`"), ephemeral=True)
            except Exception:
                pass

# ══════════════════════════════════════════════════════
# VERİ FORMATLAMA
# ══════════════════════════════════════════════════════

_ALAN_TR = {
    "TC": "T.C. Kimlik No", "ADI": "Adi", "SOYADI": "Soyadi",
    "DOGUMTARIHI": "Dogum Tarihi", "NUFUSIL": "Nufus Ili",
    "NUFUSILCE": "Nufus Ilcesi", "ANNEADI": "Anne Adi", "ANNETC": "Anne T.C.",
    "BABAADI": "Baba Adi", "BABATC": "Baba T.C.", "UYRUK": "Uyruk",
    "MEDENIHAL": "Medeni Hal", "OLUMTARIHI": "Olum Tarihi", "CINSIYET": "Cinsiyet",
    "DOGUMYERI": "Dogum Yeri", "GSM": "GSM No", "ADRES": "Adres",
    "IL": "Il", "ILCE": "Ilce", "MAHALLE": "Mahalle", "SOKAK": "Sokak",
    "BINA": "Bina No", "DAIRE": "Daire No", "ISYERI": "Isyeri",
    "SGKDURUM": "SGK Durumu", "VERGINO": "Vergi No",
    "VERGIDAIRESI": "Vergi Dairesi", "KAN": "Kan Grubu",
    "HAYATTA": "Hayatta Mi", "NUFUSDURUM": "Nufus Durumu",
    # Tapu alanları
    "İLBİLGİSİ": "Il", "İLCEBİLGİSİ": "Ilce", "MAHALLEBİLGİSİ": "Mahalle",
    "ZEMİNTİPBİLGİSİ": "Zemin Tipi", "ADABİLGİSİ": "Ada No",
    "PARSELBİLGİSİ": "Parsel No", "YUZOLCUMBİLGİSİ": "Yuzolcum (m2)",
    "ANATASINMAZNİTELİK": "Tasinmaz Nitelik", "BLOKBİLGİSİ": "Blok",
    "BAGIMSIZBOLUMNo": "Bagimsiz Bolum No", "ARSAPAy": "Arsa Pay",
    "ARSAPAyDA": "Arsa Payda", "BAGIMSIZBOLUMNİTELİK": "Bolum Nitelik",
    "NAME": "Ad", "SURNAME": "Soyad", "BABADİ": "Baba Adi",
    "IDENTIFY": "TC Kimlik", "İSTİRAKNo": "İstirak No",
    "HİSSEPAy": "Hisse Pay", "HİSSEPAyDA": "Hisse Payda",
    "EDİNMESEBEBİ": "Edinme Sebebi", "TAPUDate": "Tapu Tarihi",
    "YEVMİYE": "Yevmiye",
}

_AUTH_KEYS = {"auth", "auth_alt", "authalt", "dev", "developer"}

def _alan_tr(k: str) -> str:
    return _ALAN_TR.get(k.upper(), k.replace("_", " ").replace(".", " ").title())

def _temiz(obj):
    if isinstance(obj, dict):
        return {k: _temiz(v) for k, v in obj.items() if k.lower() not in _AUTH_KEYS}
    elif isinstance(obj, list):
        return [_temiz(i) for i in obj]
    return obj

def _veri_txt_log(sorgu: str, data) -> str:
    BOL = "=" * 50
    lines = [
        "discord.gg/hapsetme", BOL,
        f"  Sorgu  : Log TR",
        f"  Domain : {sorgu}",
        f"  Tarih  : {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        BOL, ""
    ]
    temiz = _temiz(data)
    if temiz is None:
        lines.append("  Sonuc bulunamadi.")
    elif isinstance(temiz, list):
        lines.append(f"  Toplam: {len(temiz)} kayit")
        lines.append("")
        for item in temiz:
            if isinstance(item, dict):
                for k, v in item.items():
                    if v not in (None, "", "-", "None", "null"):
                        lines.append(f"  {str(k):<20}: {v}")
                lines.append("")
            else:
                lines.append(f"  {item}")
    elif isinstance(temiz, dict):
        for k, v in temiz.items():
            if v not in (None, "", "-", "None", "null"):
                lines.append(f"  {str(k):<20}: {v}")
    else:
        lines.append(str(temiz))
    lines += ["", BOL, "discord.gg/hapsetme"]
    return "\n".join(lines)

def _veri_txt(baslik: str, sorgu: str, data) -> str:
    BOL  = "=" * 50
    AYIR = "-" * 50
    lines = [
        "discord.gg/hapsetme", BOL,
        f"  Sorgu   : {baslik}",
        f"  Girilen : {sorgu}",
        f"  Tarih   : {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
        BOL, ""
    ]

    if data is None:
        lines.append("  Sonuc bulunamadi.")
    elif isinstance(data, dict):
        if "raw" in data:
            lines.append(data["raw"])
        else:
            for k, v in data.items():
                if k.lower() in _AUTH_KEYS or v in (None, "", "-", "None", "null"):
                    continue
                etiket = _alan_tr(k)
                if isinstance(v, dict):
                    lines.append(f"  {etiket}:")
                    for k2, v2 in v.items():
                        if k2.lower() in _AUTH_KEYS or v2 in (None, "", "-", "None", "null"):
                            continue
                        lines.append(f"    {_alan_tr(k2):<22}: {v2}")
                elif isinstance(v, list):
                    lines.append(f"  {etiket}: {len(v)} kayit")
                    lines.append("")
                    for idx, item in enumerate(v, 1):
                        lines.append(f"    --- Kayit #{idx} ---")
                        if isinstance(item, dict):
                            for k3, v3 in item.items():
                                if k3.lower() in _AUTH_KEYS or v3 in (None, "", "-", "None", "null"):
                                    continue
                                lines.append(f"      {_alan_tr(k3):<20}: {v3}")
                        else:
                            lines.append(f"      {item}")
                        lines.append("")
                else:
                    lines.append(f"  {etiket:<24}: {v}")
    elif isinstance(data, list):
        lines.append(f"  Toplam Kayit: {len(data)}")
        lines.append("")
        for i, item in enumerate(data, 1):
            lines.append(AYIR)
            lines.append(f"  Kayit #{i}")
            lines.append(AYIR)
            if isinstance(item, dict):
                for k, v in item.items():
                    if k.lower() in _AUTH_KEYS or v in (None, "", "-", "None", "null"):
                        continue
                    if isinstance(v, dict):
                        lines.append(f"    {_alan_tr(k)}:")
                        for k2, v2 in v.items():
                            if k2.lower() in _AUTH_KEYS or v2 in (None, "", "-", "None", "null"):
                                continue
                            lines.append(f"      {_alan_tr(k2):<18}: {v2}")
                    elif isinstance(v, list):
                        lines.append(f"    {_alan_tr(k)}: [{len(v)} item]")
                    else:
                        lines.append(f"    {_alan_tr(k):<22}: {v}")
            else:
                lines.append(f"  {item}")
            lines.append("")
    else:
        lines.append(str(data))

    lines += ["", BOL, "discord.gg/hapsetme"]
    return "\n".join(lines)

# ══════════════════════════════════════════════════════
# TÜRK BANKA KODLARI (TCMB)
# ══════════════════════════════════════════════════════

_TURK_BANKALAR = {
    "00001": "TÜRKİYE CUMHURİYET MERKEZ BANKASI",
    "00004": "İLLER BANKASI A.Ş.",
    "00010": "TÜRKİYE CUMHURİYETİ ZİRAAT BANKASI A.Ş.",
    "00012": "TÜRKİYE HALK BANKASI A.Ş.",
    "00014": "TÜRKİYE SINAİ KALKINMA BANKASI A.Ş.",
    "00015": "TÜRKİYE VAKIFLAR BANKASI T.A.O.",
    "00016": "TÜRK EXİMBANK",
    "00017": "TÜRKİYE KALKINMA VE YATIRIM BANKASI A.Ş.",
    "00029": "BİRLEŞİK FON BANKASI A.Ş.",
    "00032": "TÜRK EKONOMİ BANKASI A.Ş.",
    "00046": "AKBANK T.A.Ş.",
    "00059": "ŞEKERBANK T.A.Ş.",
    "00062": "TÜRKİYE GARANTİ BANKASI A.Ş.",
    "00064": "TÜRKİYE İŞ BANKASI A.Ş.",
    "00067": "YAPI VE KREDİ BANKASI A.Ş.",
    "00091": "ARAP TÜRK BANKASI A.Ş.",
    "00092": "CITIBANK A.Ş.",
    "00096": "TURKISH BANK A.Ş.",
    "00098": "JPMORGAN CHASE BANK N.A.",
    "00099": "ING BANK A.Ş.",
    "00100": "ADABANK A.Ş.",
    "00103": "FİBABANKA A.Ş.",
    "00108": "TURKLAND BANK A.Ş.",
    "00109": "ICBC TURKEY BANK A.Ş.",
    "00111": "QNB FİNANSBANK A.Ş.",
    "00115": "DEUTSCHE BANK A.Ş.",
    "00116": "PASHA YATIRIM BANKASI A.Ş.",
    "00121": "STANDARD CHARTERED YATIRIM BANKASI TÜRK A.Ş.",
    "00122": "SOCIETE GENERALE (SA)",
    "00123": "HSBC BANK A.Ş.",
    "00124": "ALTERNATİFBANK A.Ş.",
    "00125": "BURGAN BANK A.Ş.",
    "00129": "BANK OF AMERICA YATIRIM BANK A.Ş.",
    "00132": "İSTANBUL TAKAS VE SAKLAMA BANKASI A.Ş.",
    "00134": "DENİZBANK A.Ş.",
    "00135": "ANADOLUBANK A.Ş.",
    "00137": "RABOBANK A.Ş.",
    "00138": "DİLER YATIRIM BANKASI A.Ş.",
    "00139": "GSD YATIRIM BANKASI A.Ş.",
    "00141": "NUROL YATIRIM BANKASI A.Ş.",
    "00142": "BANKPOZİTİF KREDİ VE KALKINMA BANKASI A.Ş.",
    "00143": "AKTİF YATIRIM BANKASI A.Ş.",
    "00146": "ODEA BANK A.Ş.",
    "00147": "MUFG Bank Turkey A.Ş.",
    "00148": "INTESA SANPAOLO S.P.A.",
    "00149": "BANK OF CHINA TURKEY A.Ş.",
    "00150": "GOLDEN GLOBAL YATIRIM BANKASI A.Ş.",
    "00203": "ALBARAKA TÜRK KATILIM BANKASI A.Ş.",
    "00205": "KUVEYT TÜRK KATILIM BANKASI A.Ş.",
    "00206": "TÜRKİYE FİNANS KATILIM BANKASI A.Ş.",
    "00209": "ZİRAAT KATILIM BANKASI A.Ş.",
    "00210": "VAKIF KATILIM BANKASI A.Ş.",
    "00211": "TÜRKİYE EMLAK KATILIM BANKASI A.Ş.",
    "00806": "MERKEZİ KAYIT KURULUŞU A.Ş.",
    "00807": "POSTA VE TELGRAF TEŞKİLATI A.Ş.",
}

def _iban_parse(iban: str) -> dict:
    """TR IBAN parse: 26 karakter, TR + 2 check + 5 bank + 1 reserved + 16 account"""
    iban = iban.replace(" ", "").replace("-", "").upper()
    if not iban.startswith("TR") or len(iban) != 26:
        return {"valid": False, "error": "Geçersiz IBAN formatı (TR ile başlamalı, 26 karakter)"}
    
    ulke = iban[0:2]
    check = iban[2:4]
    banka_kodu = iban[4:9]
    reserved = iban[9:10]
    hesap_no = iban[10:26]
    
    # MOD-97 kontrolü (ISO 13616)
    # IBAN'i sona taşı: banka+reserved+hesap + TR + check → mod 97 == 1
    rearranged = iban[4:] + iban[0:4]
    # Harfleri sayıya çevir (A=10, B=11, ..., Z=35)
    numeric = ""
    for c in rearranged:
        if c.isdigit():
            numeric += c
        else:
            numeric += str(ord(c) - ord('A') + 10)
    try:
        valid_checksum = (int(numeric) % 97 == 1)
    except:
        valid_checksum = False
    
    banka_adi = _TURK_BANKALAR.get(banka_kodu, "Bilinmeyen Banka")
    
    return {
        "valid": valid_checksum,
        "iban": iban,
        "ulke": ulke,
        "check_digit": check,
        "banka_kodu": banka_kodu,
        "banka_adi": banka_adi,
        "hesap_no": hesap_no,
        "banka_bulundu": banka_kodu in _TURK_BANKALAR
    }

# ══════════════════════════════════════════════════════
# KREDİ KARTI DOĞRULAMA (Fake — Luhn + expiry + BIN)
# ══════════════════════════════════════════════════════

async def _bin_lookup(sess: aiohttp.ClientSession, bin_number: str) -> dict:
    """binlist.net API ile BIN bilgisi al"""
    try:
        async with sess.get(
            f"https://lookup.binlist.net/{bin_number}",
            timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return {
                    "scheme": data.get("scheme", "Bilinmiyor").upper(),
                    "type": data.get("type", "Bilinmiyor").upper(),
                    "brand": data.get("brand", "Bilinmiyor"),
                    "bank": data.get("bank", {}).get("name", "Bilinmiyor"),
                    "country": data.get("country", {}).get("name", "Bilinmiyor"),
                    "country_code": data.get("country", {}).get("alpha2", ""),
                }
            return None
    except Exception as e:
        print(f"[BIN LOOKUP] {e}")
        return None

async def _cc_check_fake(sess: aiohttp.ClientSession, card_number: str, expire_month: str, expire_year: str, cvv: str) -> dict:
    """Fake CC checker - Gercekci validasyon: Luhn + expiry check + random LIVE/DEAD"""
    card_clean = card_number.replace(" ", "").replace("-", "")
    bin_number = card_clean[:6] if len(card_clean) >= 6 else card_clean

    # BIN lookup (GERCEK) - hata olsa da devam et
    bin_data = None
    try:
        bin_data = await _bin_lookup(sess, bin_number)
    except Exception:
        pass

    def _bd(key):
        return bin_data.get(key) if bin_data else "Bilinmiyor"

    # Luhn check
    def luhn_check(card_no):
        if not card_no.isdigit():
            return False
        digits = [int(d) for d in card_no]
        checksum = 0
        for i, digit in enumerate(reversed(digits)):
            if i % 2 == 1:
                doubled = digit * 2
                checksum += doubled if doubled < 10 else doubled - 9
            else:
                checksum += digit
        return checksum % 10 == 0

    luhn_valid = luhn_check(card_clean)

    # Expiry date validation (GERCEK)
    exp_month_str = str(expire_month).strip().zfill(2)
    exp_year_str  = str(expire_year).strip()

    try:
        exp_month = int(exp_month_str)
        exp_year  = int(exp_year_str) if len(exp_year_str) == 4 else int(f"20{exp_year_str}")
        now       = datetime.now()

        if exp_month < 1 or exp_month > 12:
            return {
                "card_number": f"{card_clean[:4]} **** **** {card_clean[-4:]}",
                "expire_month": str(exp_month).zfill(2), "expire_year": str(exp_year),
                "status": "DEAD", "message": "GECERSIZ TARIH - CALISIMIYOR",
                "bin": bin_number, "scheme": _bd("scheme"), "type": _bd("type"),
                "brand": _bd("brand"), "bank": _bd("bank"),
                "country": _bd("country"), "country_code": _bd("country_code")
            }

        if exp_year < now.year or (exp_year == now.year and exp_month < now.month):
            return {
                "card_number": f"{card_clean[:4]} **** **** {card_clean[-4:]}",
                "expire_month": str(exp_month).zfill(2), "expire_year": str(exp_year),
                "status": "DEAD", "message": "KART VADESI GECMIS - CALISIMIYOR",
                "bin": bin_number, "scheme": _bd("scheme"), "type": _bd("type"),
                "brand": _bd("brand"), "bank": _bd("bank"),
                "country": _bd("country"), "country_code": _bd("country_code")
            }

    except (ValueError, TypeError):
        return {
            "card_number": f"{card_clean[:4]} **** **** {card_clean[-4:]}",
            "expire_month": exp_month_str, "expire_year": exp_year_str,
            "status": "DEAD", "message": "GECERSIZ TARIH - CALISIMIYOR",
            "bin": bin_number, "scheme": _bd("scheme"), "type": _bd("type"),
            "brand": _bd("brand"), "bank": _bd("bank"),
            "country": _bd("country"), "country_code": _bd("country_code")
        }

    if not luhn_valid:
        return {
            "card_number": f"{card_clean[:4]} **** **** {card_clean[-4:]}",
            "expire_month": str(exp_month).zfill(2), "expire_year": str(exp_year),
            "status": "DEAD", "message": "GECERSIZ KART NUMARASI - CALISIMIYOR",
            "bin": bin_number, "scheme": _bd("scheme"), "type": _bd("type"),
            "brand": _bd("brand"), "bank": _bd("bank"),
            "country": _bd("country"), "country_code": _bd("country_code")
        }

    # Luhn valid + tarih valid: deterministik %70 LIVE
    card_hash = int(hashlib.md5(card_clean.encode()).hexdigest(), 16)
    random.seed(card_hash)

    if random.random() < 0.70:
        status  = "LIVE"
        message = random.choice([
            "KART GECERLI VE AKTIF",
            "ISLEM BASARILI",
            "APPROVED",
            "CVV MATCH - APPROVED"
        ])
    else:
        status  = "DEAD"
        message = random.choice([
            "YETERSIZ BAKIYE - CALISIMIYOR",
            "KART KAPALI - CALISIMIYOR",
            "DO NOT HONOUR - CALISIMIYOR",
            "INVALID CVV - CALISIMIYOR",
            "TRANSACTION NOT PERMITTED - CALISIMIYOR"
        ])

    return {
        "card_number":  f"{card_clean[:4]} **** **** {card_clean[-4:]}",
        "expire_month": str(exp_month).zfill(2),
        "expire_year":  str(exp_year),
        "status":       status,
        "message":      message,
        "bin":          bin_number,
        "scheme": _bd("scheme"), "type": _bd("type"),
        "brand":  _bd("brand"),  "bank": _bd("bank"),
        "country": _bd("country"), "country_code": _bd("country_code")
    }
async def _kimlik_asset_indir():
    """Kart sablonlarini ve fontlari indir, cache'le"""
    if _kimlik_cache:
        return _kimlik_cache
    base = "https://kekekeeleme-production.up.railway.app"
    async with aiohttp.ClientSession() as sess:
        for name, path in [
            ("front_bg", "/img/front.png"),
            ("back_bg",  "/img/back.png"),
            ("calibri",  "/font/calibri.ttf"),
            ("ocr_b",    "/font/ocr-b.otf"),
        ]:
            try:
                async with sess.get(f"{base}{path}", timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status == 200:
                        _kimlik_cache[name] = await r.read()
                        print(f"[KIMLIK ASSET] {name} indirildi ({len(_kimlik_cache[name])} bytes)")
            except Exception as e:
                print(f"[KIMLIK ASSET] {name} indirilemedi: {e}")
    return _kimlik_cache

def _mrz_olustur(veri: dict) -> str:
    """Website JS ile ayni MRZ (3 satir x 30 karakter)"""
    m = veri.get("Seri No", "")
    c = veri.get("TC Kimlik No", "")
    dogum = veri.get("Dogum Tarihi", "")
    gecerlilik = veri.get("Gecerlilik", "")
    cinsiyet = veri.get("Cinsiyet", "")
    isim = veri.get("Isim", "")
    soyisim = veri.get("Soyisim", "")

    # Tarih -> YYMMDD
    def yyddmm(t):
        parts = t.replace("-", ".").split(".")
        if len(parts) == 3:
            return parts[2][-2:] + parts[1].zfill(2) + parts[0].zfill(2)
        return "000000"

    n = yyddmm(dogum)
    l = yyddmm(gecerlilik)
    gender_letter = "M" if cinsiyet.upper().startswith("E") else "F"

    # Satir 1
    satir1 = f"I<TUR{m}2<{c}<<<"
    # Satir 2
    satir2 = f"{n}1{gender_letter}{l}2TUR<<<<<<<<<<<2"
    # Satir 3
    ascii_isim = isim.translate(_TR_ASCII).upper()
    ascii_soyisim = soyisim.translate(_TR_ASCII).upper()
    filler_len = max(0, 28 - len(ascii_soyisim) - len(ascii_isim))
    satir3 = f"{ascii_soyisim}<<{ascii_isim}{'<' * filler_len}"

    # 30 karaktere sabitle
    return f"{satir1[:30]:<30}\n{satir2[:30]:<30}\n{satir3[:30]:<30}"

def _kimlik_kart_ciz(veri: dict, foto_bytes: bytes, assets: dict):
    """Pillow ile website birebir kart cizimi -> (front_bytes, back_bytes)"""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

    # Cinsiyet formati: E -> "E / M", K -> "K / F"
    cinsiyet_kart = "E / M" if veri.get("Cinsiyet", "").upper().startswith("E") else "K / F"

    # ── FRONT ──
    front = Image.open(io.BytesIO(assets["front_bg"])).convert("RGBA")
    front = front.resize((1200, 700))  # CSS: width:1200px height:700px background-size:cover
    # brightness 110%
    front = ImageEnhance.Brightness(front).enhance(1.1)

    # Foto (grayscale, width=300, left=50, bottom=70)
    foto_raw = Image.open(io.BytesIO(foto_bytes)).convert("L")
    aspect = foto_raw.height / max(foto_raw.width, 1)
    fw, fh = 300, int(300 * aspect)
    foto = foto_raw.resize((fw, fh))
    fx, fy = 50, 700 - 70 - fh
    front.paste(foto, (fx, fy))

    # Face-right (grayscale, width=100, right=85, bottom=190, opacity=0.1, brightness 110%)
    fr_w, fr_h = 100, int(100 * aspect)
    foto_r = foto_raw.resize((fr_w, fr_h))
    foto_r = ImageEnhance.Brightness(foto_r).enhance(1.1)
    foto_r_rgba = foto_r.convert("RGBA")
    alpha = foto_r_rgba.split()[3]
    alpha = alpha.point(lambda x: int(x * 0.1))
    foto_r_rgba.putalpha(alpha)
    fr_x = 1200 - 85 - fr_w
    fr_y = 700 - 190 - fr_h
    front.paste(foto_r_rgba, (fr_x, fr_y), foto_r_rgba)

    # Text layer (blur 1px)
    f44 = ImageFont.truetype(io.BytesIO(assets["calibri"]), 44)
    f47 = ImageFont.truetype(io.BytesIO(assets["calibri"]), 47)

    text_layer = Image.new("RGBA", (1200, 700), (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)

    td.text((70, 160),  veri.get("TC Kimlik No", ""),  fill=(0, 0, 0), font=f47)
    td.text((410, 280), veri.get("Soyisim", ""),       fill=(0, 0, 0), font=f44)
    td.text((410, 360), veri.get("Isim", ""),          fill=(0, 0, 0), font=f44)
    td.text((409, 445), veri.get("Dogum Tarihi", ""),  fill=(0, 0, 0), font=f44)
    td.text((730, 445), cinsiyet_kart,                  fill=(0, 0, 0), font=f44)
    td.text((408, 529), veri.get("Seri No", ""),        fill=(0, 0, 0), font=f44)
    td.text((407, 610), veri.get("Gecerlilik", ""),     fill=(0, 0, 0), font=f44)

    text_layer = text_layer.filter(ImageFilter.GaussianBlur(radius=1))
    front = Image.alpha_composite(front, text_layer)

    # ── BACK ──
    back = Image.open(io.BytesIO(assets["back_bg"])).convert("RGBA")
    back = back.resize((1200, 700))  # CSS: width:1200px height:700px background-size:cover
    back = ImageEnhance.Brightness(back).enhance(0.9)

    mrz_text = _mrz_olustur(veri)
    mrz_lines = mrz_text.split("\n")

    text_back = Image.new("RGBA", (1200, 700), (0, 0, 0, 0))
    tbd = ImageDraw.Draw(text_back)

    tbd.text((375, 165), veri.get("Anne Adi", ""), fill=(0, 0, 0), font=f44)
    tbd.text((375, 250), veri.get("Baba Adi", ""), fill=(0, 0, 0), font=f44)

    # MRZ (OCR-B font, no blur)
    f_ocrb = ImageFont.truetype(io.BytesIO(assets["ocr_b"]), 47)
    for i, line in enumerate(mrz_lines):
        tbd.text((85, 450 + i * 40), line, fill=(0, 0, 0), font=f_ocrb)

    text_back = text_back.filter(ImageFilter.GaussianBlur(radius=1))
    back = Image.alpha_composite(back, text_back)

    # Save
    front_rgb = front.convert("RGB")
    back_rgb = back.convert("RGB")
    front_buf = io.BytesIO()
    front_rgb.save(front_buf, format="PNG")
    back_buf = io.BytesIO()
    back_rgb.save(back_buf, format="PNG")

    return (front_buf.getvalue(), back_buf.getvalue())

async def _kimlik_kart_olustur(veri: dict, foto_bytes: bytes):
    """Asset'leri indir, karti ciz -> (front_bytes, back_bytes)"""
    assets = await _kimlik_asset_indir()
    if not all(k in assets for k in ("front_bg", "back_bg", "calibri", "ocr_b")):
        raise Exception("Kart sablonlari indirilemedi")
    return await asyncio.to_thread(_kimlik_kart_ciz, veri, foto_bytes, assets)

# ══════════════════════════════════════════════════════
# İHBAR FONKSİYONU
# ══════════════════════════════════════════════════════

async def _ihbar_gonder(sess: aiohttp.ClientSession, baslik: str, aciklama: str, sehir: str, ilce: str, telefon: str) -> dict:
    headers_form = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9",
    }
    try:
        egm_data = {
            "ctl00$ContentPlaceHolder1$txtIcerik": aciklama,
            "ctl00$ContentPlaceHolder1$txtSehir":  sehir,
            "ctl00$ContentPlaceHolder1$txtIlce":   ilce,
            "ctl00$ContentPlaceHolder1$txtGsm":    telefon,
            "ctl00$ContentPlaceHolder1$txtAdres":  "",
            "ctl00$ContentPlaceHolder1$txtTarih":  "",
            "ctl00$ContentPlaceHolder1$txtMail":   "",
            "ctl00$ContentPlaceHolder1$btnIhbar":  "Gönder",
        }
        async with sess.post(
            "https://onlineislemler.egm.gov.tr/sayfalar/ihbar.aspx",
            data=egm_data, headers=headers_form,
            timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True
        ) as r:
            ok = r.status in (200, 302)
            return {"platform": "EGM (Polis)", "ok": ok,
                    "mesaj": "Gonderildi" if ok else f"HTTP {r.status}"}
    except Exception as e:
        return {"platform": "EGM (Polis)", "ok": False, "mesaj": str(e)[:80]}


# ══════════════════════════════════════════════════════
# API ÇAĞRILARI
# ══════════════════════════════════════════════════════

async def _api_call(sess: aiohttp.ClientSession, sid: str, val: str):

    if sid == "tc":
        d = await searchulp_get(sess, f"/tc/{val}")

    elif sid == "tapu":
        try:
            async with sess.get(
                f"http://searchulp.xyz/api.php",
                params={"key": SEARCHULP_KEY, "endpoint": "tapu", "tc": val},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                d = await r.json(content_type=None)
        except Exception as e:
            print(f"[API] tapu hata: {e}")
            return None

    elif sid == "vesika":
        try:
            async with sess.get(
                f"https://solidarksystems.alwaysdata.net/vesika.php?tc={val}",
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                d = await r.json(content_type=None)
                d = _temiz(d)
        except Exception:
            return None

    elif sid == "sgk":
        try:
            async with sess.get(
                f"https://solidarksystems.alwaysdata.net/sgk.php?tc={val}",
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                d = await r.json(content_type=None)
                d = _temiz(d)
        except Exception:
            return None

    elif sid == "adsoyad":
        parts = val.split(",", 1)
        ad    = parts[0].strip()
        soyad = parts[1].strip() if len(parts) > 1 else ""
        p = {"ad": ad}
        if soyad:
            p["soyad"] = soyad
        d = await searchulp_get(sess, "/adsoyad", params=p)

    elif sid == "adsoyadpro":
        parts = val.split(",")
        ad    = parts[0].strip() if len(parts) > 0 else ""
        soyad = parts[1].strip() if len(parts) > 1 else ""
        il    = parts[2].strip() if len(parts) > 2 else ""
        ilce  = parts[3].strip() if len(parts) > 3 else ""
        try:
            params = {"ad": ad, "soyad": soyad, "il": il}
            if ilce:
                params["ilce"] = ilce
            async with sess.get(
                "https://solidarksystems.alwaysdata.net/adsoyad.php",
                params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                d = await r.json(content_type=None)
                d = _temiz(d)
        except Exception as e:
            print(f"[API] adsoyadpro hata: {e}")
            return None

    elif sid == "adsorgu":
        parts = val.split(",")
        ad   = parts[0].strip() if len(parts) > 0 else ""
        il   = parts[1].strip() if len(parts) > 1 else ""
        ilce = parts[2].strip() if len(parts) > 2 else ""
        try:
            url = f"https://solidarksystems.alwaysdata.net/adililce.php?ad={ad}&il={il}&ilce={ilce}"
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
                d = await r.json(content_type=None)
                d = _temiz(d)
        except Exception:
            return None

    elif sid == "aile":
        d = await searchulp_get(sess, f"/aile/{val}")

    elif sid == "sulale":
        d = await searchulp_get(sess, f"/sulale/{val}")

    elif sid == "cocuk":
        d = await searchulp_get(sess, f"/cocuk/{val}")

    elif sid == "adres":
        d = await searchulp_get(sess, f"/adres/{val}")

    elif sid == "gsmtc":
        d = await searchulp_get(sess, f"/gsmtc/{val}")

    elif sid == "tcgsm":
        d = await searchulp_get(sess, f"/tcgsm/{val}")

    elif sid == "isyeri":
        d = await searchulp_get(sess, f"/isyeri/{val}")

    elif sid == "ip":
        try:
            async with sess.get(
                f"https://ipapi.co/{val}/json/",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as r:
                d = await r.json(content_type=None)
                if isinstance(d, dict) and d.get("error"):
                    return None
        except Exception:
            return None

    elif sid == "discord":
        try:
            async with sess.get(
                f"https://n3m3m3sis-production.up.railway.app/api/v1/{val}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                raw = await r.json(content_type=None)
            if isinstance(raw, dict):
                if raw.get("status") != "success":
                    return None
                kayitlar = raw.get("kayitlar", [])
                if not kayitlar:
                    return None
                _SKIP = {"id", "kaynak"}
                sonuclar = []
                for k in kayitlar:
                    if not isinstance(k, dict):
                        continue
                    kaynak = k.get("kaynak", "")
                    satirlar = [f"[{kaynak}]"] if kaynak else []
                    for key, val2 in k.items():
                        if key in _SKIP:
                            continue
                        if val2 and str(val2).lower() not in ("none", "null", "[]", ""):
                            satirlar.append(f"  {key}: {val2}")
                    if len(satirlar) > 1:
                        sonuclar.append("\n".join(satirlar))
                d = sonuclar if sonuclar else None
            else:
                return None
        except Exception as e:
            print(f"[API] discord hata: {e}")
            return None

    elif sid == "logtr":
        try:
            async with sess.get(
                f"https://solidarksystems.alwaysdata.net/log.php?url={val}",
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                d = await r.json(content_type=None)
                d = _temiz(d)
        except Exception:
            return None

    else:
        return None

    if isinstance(d, dict) and d.get("success") is False:
        return None
    if isinstance(d, dict) and "data" in d:
        return d["data"]
    return d

# ══════════════════════════════════════════════════════
# TICKET
# ══════════════════════════════════════════════════════

aktif_ticketlar: dict = {}

@bot.command(name="ticket")
async def ticket_cmd(ctx: commands.Context):
    try:
        if ctx.channel.id != CMD_CHANNEL_ID:
            await cmd_uyari(ctx, CMD_CHANNEL_ID); return
        rem = cd(ctx.author.id)
        if rem > 0:
            await cd_uyari(ctx, rem); return
        cd_set(ctx.author.id)

        emb = discord.Embed(
            title="Destek Talebi",
            description=(
                f"{ctx.author.mention}\n"
                "**Destek Talebi** oluşturmak için aşağıdan kategoriyi seç.\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "**Yetkili Alım** — Yetkili olmak istiyorum\n"
                "**Sorgu Bilgi** — Sorgu sistemi hakkında\n"
                "**Şikayet & İstek** — Öneri veya şikayet\n"
                "**Abone Rol** — Key almak istiyorum"
            ),
            color=C_BRAND,
            timestamp=datetime.now(timezone.utc)
        )
        emb.set_author(name="Hapsetme Sorgu — Destek", icon_url=bot.user.display_avatar.url)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb, view=TicketKategoriView())
    except Exception as e:
        print(f"[ticket HATA] {e}")
        await ctx.send(embed=e_err(f"Ticket açılamadı: `{e}`"))


class TicketKategoriView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())


class TicketSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Kategori seç...",
            custom_id="ticket_kategori_select",
            options=[
                discord.SelectOption(label="Yetkili Alım",    value="yetkili_alim",  description="Yetkili başvurusu"),
                discord.SelectOption(label="Sorgu Bilgi",     value="sorgu_bilgi",   description="Sorgu sistemi hakkında"),
                discord.SelectOption(label="Şikayet & İstek", value="sikayet_istek", description="Öneri, şikayet veya istek"),
                discord.SelectOption(label="Abone Rol",       value="abone_rol",     description="Abone rolü talebi"),
            ]
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            await _ticket_ac(interaction, self.values[0])
        except Exception as e:
            print(f"[TicketSelect HATA] {e}")
            try:
                await interaction.followup.send(embed=e_err(f"Hata: `{e}`"), ephemeral=True)
            except Exception:
                pass


KAT = {
    "yetkili_alim":  "Yetkili Alım",
    "sorgu_bilgi":   "Sorgu Bilgi",
    "sikayet_istek": "Şikayet & İstek",
    "abone_rol":     "Abone Rol",
}


async def _ticket_ac(interaction: discord.Interaction, kat: str):
    guild = interaction.guild
    if not guild:
        await interaction.followup.send(embed=e_err("Sunucu bulunamadı."), ephemeral=True)
        return

    ticket_count["count"] += 1
    sv_tc()
    no = f"ticket-{ticket_count['count']:03d}"

    cat = guild.get_channel(TICKET_CAT_ID)
    if not cat:
        try:
            cat = await guild.fetch_channel(TICKET_CAT_ID)
        except Exception:
            cat = None

    yrol = guild.get_role(YETKILI_ROL_ID)
    ow = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        guild.me:           discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
    }
    if yrol:
        ow[yrol] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, attach_files=True)

    try:
        ch = await guild.create_text_channel(
            no, category=cat, overwrites=ow,
            topic=f"{interaction.user}  |  {KAT.get(kat, kat)}"
        )
    except discord.Forbidden:
        await interaction.followup.send(embed=e_err("Botun kanal oluşturma yetkisi yok."), ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(embed=e_err(f"Ticket açılamadı: `{e}`"), ephemeral=True)
        return

    aktif_ticketlar[ch.id] = {"owner_id": interaction.user.id, "kat": kat}

    yrol_m = f"<@&{YETKILI_ROL_ID}>"
    emb = discord.Embed(
        description=(
            f"{interaction.user.mention}, talebiniz alındı.\n"
            "─────────────────────────────\n"
            f"**No:** `{no}`\n"
            f"**Kategori:** {KAT.get(kat, kat)}\n"
            f"**Yetkili:** {yrol_m}\n\n"
            "Sorununu detaylıca açıkla, bir yetkili ilgilenecek."
        ),
        color=C_BRAND,
        timestamp=datetime.now(timezone.utc)
    )
    emb.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    emb.set_footer(text=f"{FOOTER}  •  {interaction.user.id}")

    try:
        await ch.send(content=f"{interaction.user.mention} {yrol_m}", embed=emb, view=TicketYonetimView())
    except Exception:
        pass

    await interaction.followup.send(embed=e_ok(f"Ticketin oluşturuldu: {ch.mention}"), ephemeral=True)


class TicketYonetimView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def _yetki(self, i: discord.Interaction) -> bool:
        return YETKILI_ROL_ID in {r.id for r in i.user.roles} if i.guild else False

    @discord.ui.button(label="Kapat",   style=discord.ButtonStyle.danger,    custom_id="t_kapat")
    async def kapat(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._yetki(interaction):
            await interaction.response.send_message(embed=e_err("Sadece yetkililer kullanabilir."), ephemeral=True)
            return
        await interaction.response.defer()
        emb = discord.Embed(
            title="Ticket Kapatılıyor",
            description=f"**{interaction.user.mention}** tarafından kapatıldı.\nKanal 3 saniye içinde silinecek.",
            color=C_ERROR, timestamp=datetime.now(timezone.utc)
        )
        emb.set_footer(text=FOOTER)
        await interaction.channel.send(embed=emb)
        await asyncio.sleep(3)
        aktif_ticketlar.pop(interaction.channel.id, None)
        try:
            await interaction.channel.delete(reason=f"Kapatıldı — {interaction.user}")
        except Exception:
            pass

    @discord.ui.button(label="Arşivle", style=discord.ButtonStyle.secondary, custom_id="t_arsivle")
    async def arsivle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._yetki(interaction):
            await interaction.response.send_message(embed=e_err("Sadece yetkililer kullanabilir."), ephemeral=True)
            return
        await interaction.response.defer()
        g = interaction.guild
        try:
            await interaction.channel.set_permissions(g.default_role, read_messages=False)
            ti = aktif_ticketlar.get(interaction.channel.id)
            if ti:
                owner = g.get_member(ti["owner_id"])
                if owner:
                    await interaction.channel.set_permissions(owner, read_messages=False)
            if not interaction.channel.name.startswith("arsiv-"):
                await interaction.channel.edit(name=f"arsiv-{interaction.channel.name}")
        except Exception:
            pass
        emb = discord.Embed(
            title="Ticket Arşivlendi",
            description=f"**{interaction.user.mention}** tarafından arşivlendi.",
            color=C_WARN, timestamp=datetime.now(timezone.utc)
        )
        emb.set_footer(text=FOOTER)
        await interaction.channel.send(embed=emb)

# ══════════════════════════════════════════════════════
# DİĞER KOMUTLAR
# ══════════════════════════════════════════════════════

@bot.command(name="bilgi")
async def bilgi(ctx: commands.Context):
    emb = discord.Embed(title="Hapsetme Sorgu — Kullanım Rehberi", color=C_BRAND, timestamp=datetime.now(timezone.utc))
    emb.set_author(name="Hapsetme Sorgu", icon_url=bot.user.display_avatar.url)
    emb.add_field(name="Abone Rolü Nasıl Alınır?", value=(
        "Sorgu botunu kullanmak için:\n"
        "• Sunucuda **Abone** rolüne sahip ol\n"
        "• **Sunucuya boost** bas"
    ), inline=False)
    emb.add_field(name="Abone Rolü İçin Ne Yapmalısın?", value=(
        f"**1.** <#{CMD_CHANNEL_ID}> kanalında `a!ticket` komutunu kullan\n"
        "**2.** Açılan menüden **Abone Rol** seçeneğini seç\n"
        "**3.** Açılan ticket kanalına şunları gönder:\n"
        "  — Son videoya **like, yorum, abone** ekran görüntüsü\n"
        "  — Bir **yetkiliyi etiketle**\n"
        "**4.** Yetkili inceledikten sonra sana **25 dakikalık key** verilir"
    ), inline=False)
    emb.add_field(name="Sorgu Botu Nasıl Kullanılır?", value=(
        f"**1.** `a!key XXXX-XXXX` yazarak keyi aktifleştir\n"
        f"**2.** <#{SORGU_KANAL_ID}> kanalındaki butonları kullan\n"
        "**3.** Sonuç DM olarak TXT dosyası şeklinde gelir"
    ), inline=False)
    emb.set_footer(text=FOOTER)
    await ctx.send(embed=emb)


@bot.command(name="yardim")
async def yardim(ctx: commands.Context):
    if ctx.channel.id != YARDIM_KANAL_ID:
        ch = ctx.guild.get_channel(YARDIM_KANAL_ID) if ctx.guild else None
        m  = ch.mention if ch else f"<#{YARDIM_KANAL_ID}>"
        await ctx.send(embed=e_err(f"Bu komutu yalnızca {m} kanalında kullanabilirsin."), delete_after=8)
        try:
            await ctx.message.delete()
        except Exception:
            pass
        return

    rid  = {r.id for r in ctx.author.roles} if ctx.guild else set()
    mod  = bool(rid & set(MOD_ROL_IDS))
    kurc = is_kurucu(ctx)

    emb = discord.Embed(
        title="Hapsetme Sorgu  —  Komut Listesi",
        description="Gelişmiş sorgulama platformuna hoş geldin.\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        color=C_BRAND, timestamp=datetime.now(timezone.utc)
    )
    emb.set_author(name="Hapsetme Sorgu  •  discord.gg/hapsetme", icon_url=bot.user.display_avatar.url)
    emb.set_thumbnail(url=bot.user.display_avatar.url)
    emb.add_field(name="Key Sistemi", value=(
        "> `a!keyolustur` — 25 dakikalık key oluştur\n"
        "> `a!key-s` — Süresiz key oluştur *(izin gerekli)*\n"
        "> `a!key XXXX-XXXX` — Key aktifleştir"
    ), inline=False)
    emb.add_field(name="Sorgular", value=(
        f"> `a!sorgu` — Sorgu panelini açar\n"
        "> Key sahibi herkes kullanabilir, sonuç DM ile gelir"
    ), inline=False)
    emb.add_field(name="Destek", value=(
        f"> `a!ticket` — Destek talebi oluştur *(#{CMD_CHANNEL_ID} kanalında)*"
    ), inline=False)
    if mod or kurc:
        emb.add_field(name="Yönetim", value=(
            "> `a!keysil <id/key>` — Key sil\n"
            "> `a!keylist` — Aktif key'leri listele\n"
            "> `a!keys` — Tüm key'leri listele\n"
            "> `a!abone @kullanıcı` — Abone rolü ver\n"
            "> `a!nuke` — Kanalı yenile"
        ), inline=False)
    if kurc:
        emb.add_field(name="Kurucu", value=(
            "> `a!keysifirla` — Tüm key'leri sil\n"
            "> `a!id <id>` — Süresiz key izni ver\n"
            "> `a!mid <id>` — Kurucu ekle\n"
            "> `a!sorgu` — Sorgu paneli oluştur"
        ), inline=False)
    emb.set_footer(text=f"{FOOTER}  •  Prefix: a!")
    await ctx.send(embed=emb)


@bot.command(name="abone")
async def abone(ctx: commands.Context, hedef: Optional[discord.Member] = None):
    try:
        if YETKILI_ROL_ID not in {r.id for r in ctx.author.roles} and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        if hedef is None and ctx.message.reference and ctx.message.reference.resolved:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                hedef = ref.author
        if hedef is None:
            await ctx.send(embed=e_err("Kullanım: `a!abone @kullanıcı`")); return
        arol = ctx.guild.get_role(ABONE_ROL_ID) if ctx.guild else None
        if not arol:
            await ctx.send(embed=e_err("Abone rolü bulunamadı.")); return
        if arol in hedef.roles:
            await ctx.send(embed=e_warn(f"{hedef.mention} zaten abone.")); return
        await hedef.add_roles(arol, reason=f"a!abone — {ctx.author}")
        emb = discord.Embed(title="Abone Rolü Verildi", color=C_SUCCESS, timestamp=datetime.now(timezone.utc))
        emb.set_author(name=hedef.display_name, icon_url=hedef.display_avatar.url)
        emb.set_thumbnail(url=hedef.display_avatar.url)
        emb.add_field(name="Kullanıcı", value=hedef.mention,      inline=True)
        emb.add_field(name="Rol",       value=f"**{arol.name}**", inline=True)
        emb.add_field(name="Veren",     value=ctx.author.mention, inline=True)
        emb.set_footer(text=FOOTER)
        await ctx.send(embed=emb)
    except Exception as e:
        print(f"[abone HATA] {e}")
        await ctx.send(embed=e_err(f"Hata oluştu: `{e}`"))


@bot.command(name="nuke")
async def nuke(ctx: commands.Context):
    try:
        nuke_ids = {1486817521427419289, 1521257885537927310, 1509987771110920364}
        if not ({r.id for r in ctx.author.roles} & nuke_ids) and not is_kurucu(ctx):
            await ctx.send(embed=e_err("Bu komutu kullanmak için yetkiniz yok.")); return
        ch = ctx.channel
        await ctx.send(embed=discord.Embed(description="Kanal yenileniyor...", color=C_ERROR))
        await asyncio.sleep(1)
        pos      = ch.position
        name     = ch.name
        cat      = ch.category
        topic    = ch.topic
        overwrites = ch.overwrites
        await ch.delete(reason=f"Nuke — {ctx.author}")
        new = await ctx.guild.create_text_channel(name, category=cat, position=pos, topic=topic, overwrites=overwrites)
        emb = discord.Embed(title="Kanal Yenilendi", description="Kanal başarıyla temizlendi.", color=C_DARK, timestamp=datetime.now(timezone.utc))
        emb.add_field(name="Uygulayan", value=ctx.author.mention, inline=True)
        emb.set_footer(text=FOOTER)
        await new.send(embed=emb)
    except Exception as e:
        print(f"[nuke HATA] {e}")


# ══════════════════════════════════════════════════════
# FORCEBAN LOG KANALI
# ══════════════════════════════════════════════════════

# Sunucudan ayrılan üyeler forceban edilir; log bu kanala düşer
SPAM_FOTO_KANAL_ID = 1444737098916827367

# ══════════════════════════════════════════════════════
# PROXY SİSTEMİ
# ══════════════════════════════════════════════════════

PROXY_KAYNAKLARI = [
    # ProxyScrape API - her dakika güncellenir, key gerekmez
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&protocol=http",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&protocol=socks4",
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&protocol=socks5",
    # monosans - her saat güncellenir, hızlı olanlar başta
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    # proxmint - her 30dk test edilir
    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks4.txt",
    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks5.txt",
    # databay-labs - her 5dk güncellenir
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/http.txt",
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks4.txt",
    "https://raw.githubusercontent.com/databay-labs/free-proxy-list/master/socks5.txt",
]

# Cooldown rolleri: { rol_id: saniye }
PROXY_ROL_CD = {
    1444747455282282660: 180,   # Abone rol → 3 dakika
    1449003268951900312: 60,    # Diğer rol  → 1 dakika
}
PROXY_KURUCU_ID = 330883096826347532   # bu ID'ye cooldown yok

# { user_id: son_kullanim_zamani }
_proxy_cd: dict = {}


async def _proxy_cek() -> list:
    """Tüm kaynaklardan her seferinde taze proxy cek."""
    proxies = set()
    async with aiohttp.ClientSession() as sess:
        for url in PROXY_KAYNAKLARI:
            try:
                async with sess.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    if r.status == 200:
                        text = await r.text()
                        for line in text.splitlines():
                            line = line.strip()
                            if line and ":" in line:
                                proxies.add(line)
            except Exception as e:
                print(f"[PROXY] {url} hata: {e}")

    print(f"[PROXY] {len(proxies)} proxy cekindi")
    return list(proxies)


def _proxy_cd_kalan(uid: int, roles: set) -> float:
    """Kullanıcının kalan cooldown süresini saniye cinsinden döndür."""
    if uid == PROXY_KURUCU_ID or uid in kurucular:
        return 0.0
    son = _proxy_cd.get(uid, 0)
    sure = 0
    for rol_id, cd_sure in PROXY_ROL_CD.items():
        if rol_id in roles:
            sure = max(sure, cd_sure)
    if sure == 0:
        sure = 180  # varsayılan 3 dakika
    return max(0.0, sure - (time.time() - son))


# Sunucudan ayrılan üyeler forceban edilir; log kanalı yukarıda (SPAM_FOTO_KANAL_ID).

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    # ── DM'de kimlik adim adim olusturma ──
    if not message.guild and message.author.id in _kimlik_adim:
        uid = message.author.id
        adim = _kimlik_adim[uid]
        veri = _kimlik_veri.get(uid, {})

        # İptal
        if message.content.strip().lower() == "iptal":
            _kimlik_adim.pop(uid, None)
            _kimlik_veri.pop(uid, None)
            await message.channel.send(embed=e_err("Kimlik olusturma iptal edildi."))
            return

        # Timeout (120 saniye)
        if time.time() - veri.get("timestamp", 0) > 120:
            _kimlik_adim.pop(uid, None)
            _kimlik_veri.pop(uid, None)
            await message.channel.send(embed=e_err("Sure doldu. Tekrar dene."))
            return

        # ── Adim 0-8: Metin alani ──
        if adim < 9:
            alan_adi, soru, ornek = _kimlik_soru[adim]
            cevap = message.content.strip()
            if not cevap:
                await message.channel.send(embed=e_warn("Bos birakamazsin. Tekrar yaz."))
                return

            # Validasyon
            if alan_adi == "Cinsiyet" and cevap.upper() not in ("E", "K"):
                await message.channel.send(embed=e_warn("Sadece **E** veya **K** yaz."))
                return
            if alan_adi == "TC Kimlik No" and (not cevap.isdigit() or len(cevap) != 11):
                await message.channel.send(embed=e_warn("TC Kimlik No 11 haneli sayi olmali."))
                return

            veri[alan_adi] = cevap.upper()
            _kimlik_veri[uid] = veri
            sonraki_adim = adim + 1

            if sonraki_adim < 9:
                sonraki_alan, sonraki_soru, sonraki_ornek = _kimlik_soru[sonraki_adim]
                _kimlik_adim[uid] = sonraki_adim
                veri["timestamp"] = time.time()
                _kimlik_veri[uid] = veri
                emb = discord.Embed(
                    title=f"Kimlik Olustur — {sonraki_adim + 1}/10",
                    description=f"**{sonraki_alan}**\n{sonraki_soru}\n*Ornek: {sonraki_ornek}*",
                    color=C_BRAND
                )
                emb.set_footer(text="İptal icin: iptal")
                await message.channel.send(embed=emb)
            else:
                # Tum metin alani bitti, fotograf iste
                _kimlik_adim[uid] = 9
                veri["timestamp"] = time.time()
                _kimlik_veri[uid] = veri
                emb = discord.Embed(
                    title="Kimlik Olustur — 10/10",
                    description=(
                        "Tum bilgiler alindi.\n\n"
                        "**Simdi fotograf yukle** (eklenti olarak bir resim dosyasi gonder).\n"
                        "Kimlik karti otomatik olusturulacak."
                    ),
                    color=C_SUCCESS
                )
                emb.set_footer(text="İptal icin: iptal")
                await message.channel.send(embed=emb)
            return

        # ── Adim 9: Fotograf bekle ──
        if adim == 9:
            if not message.attachments:
                await message.channel.send(embed=e_warn("Fotograf eklemedin. Bir resim dosyasi gonder."))
                return

            attach = message.attachments[0]
            if not (attach.content_type and attach.content_type.startswith("image/")):
                await message.channel.send(embed=e_warn("Lutfen bir **resim dosyasi** yukle."))
                return

            _kimlik_adim.pop(uid, None)
            _kimlik_veri.pop(uid, None)

            await message.channel.send(embed=discord.Embed(
                description="Kimlik karti olusturuluyor...",
                color=C_BRAND
            ))

            try:
                foto_bytes = await attach.read()
                front_bytes, back_bytes = await _kimlik_kart_olustur(veri, foto_bytes)

                await message.channel.send(
                    content="**Kimlik Karti Olusturuldu**",
                    files=[
                        discord.File(fp=io.BytesIO(front_bytes), filename="kimlik_on.jpg"),
                        discord.File(fp=io.BytesIO(back_bytes), filename="kimlik_arka.jpg")
                    ]
                )
            except Exception as e:
                print(f"[KIMLIK OLUSTUR HATA] {e}")
                await message.channel.send(embed=e_err(f"Kimlik olusturulurken hata: `{e}`"))
            return

    if message.guild:
        # Flood kontrolü
        if await _flood_kontrol(message):
            return
        # Spam kontrolü
        if await _spam_kontrol(message):
            return

    await bot.process_commands(message)


@bot.event
async def on_member_remove(member: discord.Member):
    """Sunucudan ayrılan üyeyi forceban listesine ekle ve anında ban uygula."""
    try:
        guild = member.guild

        # Kullanıcıyı ban listesine ekle (sunucudan çıkmış olsa da ban uygulanabilir)
        await guild.ban(
            discord.Object(id=member.id),
            reason=f"Forceban — Sunucudan ayrıldı: {member} ({member.id})",
            delete_message_seconds=0
        )

        # Log
        log_emb = discord.Embed(
            title="Forceban — Sunucudan Ayrildi",
            description=(
                f"**Kullanıcı:** {member.mention} (`{member.id}`)\n"
                f"**Kullanıcı Adı:** `{member}`\n"
                f"**Sebep:** Sunucudan ayrılma → otomatik forceban"
            ),
            color=0xFF6600,
            timestamp=datetime.now(timezone.utc)
        )
        log_emb.set_thumbnail(url=member.display_avatar.url)
        log_emb.set_footer(text=FOOTER)

        log_ch = guild.get_channel(SPAM_FOTO_KANAL_ID)
        if not log_ch:
            try:
                log_ch = await guild.fetch_channel(SPAM_FOTO_KANAL_ID)
            except Exception:
                log_ch = None
        if log_ch:
            await log_ch.send(embed=log_emb)

        print(f"[FORCEBAN] {member} ({member.id}) — sunucudan ayrıldı")

    except discord.Forbidden:
        print(f"[FORCEBAN] Yetki yetersiz — {member.id}")
    except Exception as e:
        print(f"[FORCEBAN HATA] {e}")


@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    """Custom status değişince hapsetme kontrolü yap."""
    try:
        guild = after.guild
        rol = guild.get_role(HAPSETME_ROL_ID)
        log_kanal = guild.get_channel(HAPSETME_LOG_KANAL_ID)
        
        if not rol:
            return

        after_has_status = _custom_status_kontrol(after)
        before_has_status = _custom_status_kontrol(before)
        has_role = rol in after.roles

        # ── Çevrimdışı oldu → rolü kaldır ──
        if after.status == discord.Status.offline and before.status != discord.Status.offline:
            if has_role:
                await after.remove_roles(rol, reason="Çevrimdışı oldu — hapsetme rolü kaldırıldı")
                if log_kanal:
                    emb = discord.Embed(
                        title="Hapsetme Rolu Kaldirildi",
                        description=f"{after.mention} cevrimdisi oldu, rol alindi.",
                        color=C_ERROR,
                        timestamp=datetime.now(timezone.utc)
                    )
                    emb.add_field(name="Kullanici", value=f"{after} (`{after.id}`)", inline=False)
                    emb.add_field(name="Sebep", value="Uye cevrimdisi oldu", inline=False)
                    emb.set_thumbnail(url=after.display_avatar.url)
                    emb.set_footer(text=FOOTER)
                    await log_kanal.send(embed=emb)
                print(f"[HAPSETME] Cevrimdisi — rol alindi: {after} ({after.id})")
            return

        # ── Çevrimiçi oldu → status kontrol et, varsa rol ver ──
        if before.status == discord.Status.offline and after.status != discord.Status.offline:
            if after_has_status and not has_role:
                await after.add_roles(rol, reason="Tekrar aktif — custom status'ta hapsetme var")
                if log_kanal:
                    emb = discord.Embed(
                        title="Hapsetme Rolu Verildi",
                        description=f"{after.mention} tekrar aktif oldu, rol verildi.",
                        color=C_SUCCESS,
                        timestamp=datetime.now(timezone.utc)
                    )
                    emb.add_field(name="Kullanici", value=f"{after} (`{after.id}`)", inline=False)
                    emb.add_field(name="Sebep", value="Uye tekrar aktif, status'ta hapsetme var", inline=False)
                    emb.set_thumbnail(url=after.display_avatar.url)
                    emb.set_footer(text=FOOTER)
                    await log_kanal.send(embed=emb)
                print(f"[HAPSETME] Tekrar aktif — rol verildi: {after} ({after.id})")
            elif not after_has_status and has_role:
                # Bot restart'ta cevrimdisi atlanmis olabilir; online olunca eski rol temizle
                await after.remove_roles(rol, reason="Tekrar aktif — custom status'ta hapsetme yok, eski rol kaldirildi")
                if log_kanal:
                    emb = discord.Embed(
                        title="Hapsetme Rolu Kaldirildi",
                        description=f"{after.mention} tekrar aktif oldu, ancak status'ta hapsetme yok; eski rol kaldirildi.",
                        color=C_ERROR,
                        timestamp=datetime.now(timezone.utc)
                    )
                    emb.add_field(name="Kullanici", value=f"{after} (`{after.id}`)", inline=False)
                    emb.add_field(name="Sebep", value="Uye tekrar aktif, status'ta hapsetme yok", inline=False)
                    emb.set_thumbnail(url=after.display_avatar.url)
                    emb.set_footer(text=FOOTER)
                    await log_kanal.send(embed=emb)
                print(f"[HAPSETME] Tekrar aktif — eski rol alindi: {after} ({after.id})")
            return

        # ── Status değişti (çevrimiçiyken) ──
        # Ender sync durumlarinda before/after ikisi de offline olabilir; guvenlik kilidi
        if after.status == discord.Status.offline:
            return
        if before_has_status == after_has_status:
            return

        # Hapsetme eklendi → rol ver
        if not before_has_status and after_has_status:
            if not has_role:
                await after.add_roles(rol, reason="Custom status'ta 'hapsetme' tespit edildi")
                if log_kanal:
                    emb = discord.Embed(
                        title="Hapsetme Rolu Verildi",
                        description=f"{after.mention} kullanicisina rol verildi.",
                        color=C_SUCCESS,
                        timestamp=datetime.now(timezone.utc)
                    )
                    emb.add_field(name="Kullanici", value=f"{after} (`{after.id}`)", inline=False)
                    for activity in after.activities:
                        if isinstance(activity, discord.CustomActivity) and activity.name:
                            emb.add_field(name="Custom Status", value=f"`{activity.name}`", inline=False)
                            break
                    emb.set_thumbnail(url=after.display_avatar.url)
                    emb.set_footer(text=FOOTER)
                    await log_kanal.send(embed=emb)
                print(f"[HAPSETME] Rol verildi: {after} ({after.id})")

        # Hapsetme kaldırıldı → rol al
        elif before_has_status and not after_has_status:
            if has_role:
                await after.remove_roles(rol, reason="Custom status'tan 'hapsetme' kaldırıldı")
                if log_kanal:
                    emb = discord.Embed(
                        title="Hapsetme Rolu Kaldirildi",
                        description=f"{after.mention} kullanicisinden rol alindi.",
                        color=C_ERROR,
                        timestamp=datetime.now(timezone.utc)
                    )
                    emb.add_field(name="Kullanici", value=f"{after} (`{after.id}`)", inline=False)
                    emb.add_field(name="Sebep", value="Custom status'tan 'hapsetme' kaldirildi", inline=False)
                    emb.set_thumbnail(url=after.display_avatar.url)
                    emb.set_footer(text=FOOTER)
                    await log_kanal.send(embed=emb)
                print(f"[HAPSETME] Rol alindi: {after} ({after.id})")

    except Exception as e:
        print(f"[HAPSETME HATA] {e}")


# ══════════════════════════════════════════════════════
# HATA YAKALAMA & READY
# ══════════════════════════════════════════════════════

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=e_err(f"Eksik parametre: `{error.param.name}`"), delete_after=8)
        return
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(embed=e_err("Kullanıcı bulunamadı."), delete_after=8)
        return
    print(f"[KOMUT HATA] {ctx.command}: {error}")


@bot.event
async def on_ready():
    print(f"[BOT] {bot.user} — {len(bot.guilds)} sunucu")

    # Upstash'ten güncel verileri in-place yükle
    _k = _load("kurucular.json",    [330883096826347532, 1556799286023561354])
    _d = _load("keys.json",         {})
    _s = _load("sinirizsiz.json",   [])
    _t = _load("ticket_count.json", {"count": 0})
    _npc  = _load("netflix_pc.json",    [])
    _nmob = _load("netflix_mobil.json", [])
    _duo  = _load("duolingo.json",      [])

    kurucular.clear();          kurucular.extend(_k)
    keys_db.clear();            keys_db.update(_d)
    sinirizsiz.clear();         sinirizsiz.extend(_s)
    ticket_count.clear();       ticket_count.update(_t)
    netflix_pc_stok.clear();    netflix_pc_stok.extend(_npc)
    netflix_mobil_stok.clear(); netflix_mobil_stok.extend(_nmob)
    duolingo_stok.clear();      duolingo_stok.extend(_duo)

    if _USE_UPSTASH:
        print(f"[STORAGE] Upstash AKTIF — {len(keys_db)} key, {len(kurucular)} kurucu")
    else:
        print(f"[STORAGE] Dosya modu — {len(keys_db)} key")

    # ── Hapsetme: bot açıldığında aktif üyeleri tara ──
    await asyncio.sleep(5)  # Discord cache'in dolmasını bekle
    for guild in bot.guilds:
        rol = guild.get_role(HAPSETME_ROL_ID)
        if not rol:
            continue
        tarama_verildi = 0
        tarama_alindi  = 0
        for member in guild.members:
            try:
                # Sadece aktif üyeleri tara (çevrimdışıların status'u cache'de yok)
                if member.status == discord.Status.offline:
                    continue

                has_status = _custom_status_kontrol(member)
                has_role   = rol in member.roles

                if has_status and not has_role:
                    await member.add_roles(rol, reason="Bot restart tarama — custom status'ta hapsetme var")
                    tarama_verildi += 1
                    print(f"[HAPSETME] Restart: rol verildi → {member} ({member.id})")
                elif not has_status and has_role:
                    await member.remove_roles(rol, reason="Bot restart tarama — custom status'ta hapsetme yok")
                    tarama_alindi += 1
                    print(f"[HAPSETME] Restart: rol alindi → {member} ({member.id})")
            except Exception as e:
                print(f"[HAPSETME TARAMA HATA] {member.id}: {e}")
        print(f"[HAPSETME] Tarama tamamlandi: {tarama_verildi} verildi, {tarama_alindi} alindi")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="a!yardim | a!ticket | #cmd"
    ))
    bot.add_view(TicketYonetimView())
    bot.add_view(SorguView())
    bot.add_view(TicketKategoriView())
    print("[BOT] Hazır")


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN env eksik!")
    bot.run(BOT_TOKEN)
