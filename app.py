import io
import os
import re
import tarfile
import time
import imaplib
import json
import html
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import docker
import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
APP_VERSION = "2.0.0"
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
CONFIG_FILE = DATA_DIR / "config.json"
KEY_FILE = DATA_DIR / ".secret.key"

SERVICES = [
    {"id": "comunicati", "name": "Comunicati stampa", "icon": "mail", "container": "automazione-articoli", "port": 8088, "description": "Email e pubblicazione comunicati", "mail_prefix": "COMUNICATI"},
    {"id": "instagram", "name": "Instagram", "icon": "instagram", "container": "igbot", "port": 8080, "description": "Bot Instagram e log pubblicazioni"},
    {"id": "amazon", "name": "Amazon", "icon": "shopping", "container": "amazon-articoli-mp", "port": 8085, "description": "Articoli affiliati e scadenze"},
    {"id": "carburanti", "name": "Prezzi carburanti", "icon": "fuel", "container": "prezzi-carburanti-montagne-paesi", "port": 8087, "description": "Ultimo aggiornamento benzina e diesel"},
    {"id": "meteo", "name": "Meteo", "icon": "cloud", "container": "meteo-wp", "port": 8124, "description": "Previsioni e invio a WordPress"},
    {"id": "oroscopo", "name": "Oroscopo", "icon": "stars", "container": "oroscopo-montagne-paesi", "port": 8086, "description": "Oroscopo quotidiano", "aliases": ["orscopo"]},
    {"id": "foto", "name": "Foto del giorno", "icon": "camera", "container": "foto-del-giorno", "port": 8090, "description": "Foto ricevute e in attesa", "mail_prefix": "FOTO"},
    {"id": "waha", "name": "WhatsApp / WAHA", "icon": "message", "container": "waha", "port": 3000, "description": "Canale WhatsApp: ultimi articoli inviati", "sender": "rssbot_montagne"},
    {"id": "eventi", "name": "Eventi", "icon": "calendar", "container": "mep-eventi", "port": int(os.getenv("EVENTI_PORT", "8095")), "description": "Importazione eventi nel calendario"},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def docker_client():
    try:
        return docker.from_env()
    except Exception:
        return None


def get_container(client, service):
    if not client:
        return None
    names = [service["container"], *service.get("aliases", [])]
    for name in names:
        try:
            return client.containers.get(name)
        except Exception:
            pass
    for container in client.containers.list(all=True):
        if any(name.lower() in container.name.lower() for name in names):
            return container
    return None


def cipher():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_FILE.exists():
        KEY_FILE.write_bytes(Fernet.generate_key())
        KEY_FILE.chmod(0o600)
    return Fernet(KEY_FILE.read_bytes())


def load_config():
    if not CONFIG_FILE.exists():
        return {"mailboxes": {}}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"mailboxes": {}}


def decrypt_password(value):
    if not value:
        return ""
    try:
        return cipher().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def clean_mail_text(value):
    if not value:
        return "(senza oggetto)"
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return str(value).strip()


def mail_activity(prefix, limit=8):
    saved = load_config().get("mailboxes", {}).get(prefix.lower(), {})
    host = saved.get("host") or os.getenv(f"{prefix}_IMAP_HOST")
    user = saved.get("user") or os.getenv(f"{prefix}_IMAP_USER")
    password = decrypt_password(saved.get("password")) or os.getenv(f"{prefix}_IMAP_PASSWORD")
    if not all((host, user, password)):
        return {"unread": None, "messages": []}
    try:
        port = int(saved.get("port") or os.getenv(f"{prefix}_IMAP_PORT", "993"))
        folder = saved.get("folder") or os.getenv(f"{prefix}_IMAP_FOLDER", "INBOX")
        with imaplib.IMAP4_SSL(host, port) as box:
            box.login(user, password)
            box.select(folder, readonly=True)
            status, data = box.search(None, "UNSEEN")
            if status != "OK":
                return {"unread": -1, "messages": [], "error": "Impossibile leggere la casella"}
            ids = data[0].split()
            messages = []
            for message_id in reversed(ids[-limit:]):
                fetch_status, parts = box.fetch(message_id, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE)])")
                if fetch_status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                message = message_from_bytes(parts[0][1])
                raw_date = message.get("Date", "")
                try:
                    date = parsedate_to_datetime(raw_date).isoformat()
                except (TypeError, ValueError, OverflowError):
                    date = raw_date
                messages.append({
                    "title": clean_mail_text(message.get("Subject")),
                    "from": clean_mail_text(message.get("From")),
                    "date": date,
                    "type": "email",
                })
            return {"unread": len(ids), "messages": messages}
    except Exception as exc:
        return {"unread": -1, "messages": [], "error": f"Errore email: {exc}"}


def panel_url(service):
    if not service.get("port"):
        return None
    base = os.getenv("DASHBOARD_HOST") or request.host.split(":", 1)[0]
    scheme = os.getenv("DASHBOARD_SCHEME", "http")
    return f"{scheme}://{base}:{service['port']}"


def check_http(url):
    if not url:
        return None
    try:
        response = requests.get(url, timeout=3, allow_redirects=True)
        return response.status_code < 500
    except requests.RequestException:
        return False


def parse_log_line(line):
    line = html.unescape(re.sub(r"\x1b\[[0-9;]*m", "", line)).strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\S+)\s+(.*)$", line)
    if not match:
        return None, line
    raw_date, message = match.groups()
    try:
        date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Rome"))
    except ValueError:
        date = None
    message = re.sub(r"^\[\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\]\s*", "", message.strip())
    return date, message


def short_date(date):
    return date.strftime("%d/%m/%y - %H:%M") if date else "Data non disponibile"


def readable_error(message, date):
    title = re.search(r"user_title=([^|]+)", message, re.I)
    detail = re.search(r"user_msg=([^|]+)", message, re.I)
    if title or detail:
        text = " — ".join(x.group(1).strip() for x in (title, detail) if x)
    else:
        text = re.split(r"\s*\|\s*(?:fbtrace_id|headers)=", message, maxsplit=1, flags=re.I)[0]
        text = re.sub(r"^(?:error|errore|exception|failed)\s*[:\-]?\s*", "", text, flags=re.I).strip()
    return f"🆘 {short_date(date)} · Errore: {text[:220]}"


def publication_title(message):
    patterns = [
        r'"(?:title|titolo|post_title)"\s*:\s*"([^"]+)"',
        r"Pubblicazione dalla coda:\s*[\"“]?(.+?)[\"”]?\s*$",
        r"(?:titolo|articolo)\s*[=:]\s*[\"“]?(.+?)[\"”]?\s*$",
        r"(?:post|articolo|previsioni?|oroscopo)?\s*(?:pubblicato|pubblicata|inviato|inviata|creato con successo)\s*[:\-]\s*[\"“]?(.+?)[\"”]?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return match.group(1).strip().strip('"“”').replace("“", "").replace("”", "")
    return None


def is_error_message(message):
    return bool(re.search(r"error|errore|failed|exception|traceback|too many actions|user_title=|401|403", message, re.I))


def is_success_message(message, service_id):
    if is_error_message(message):
        return False
    if service_id == "instagram" and re.search(r"Pubblicazione dalla coda", message, re.I):
        return False
    return bool(re.search(
        r"✅|pubblicat[oa](?:\s+su\s+(?:instagram|wordpress))?\s*[:\-]|pubblicat[oa].*(?:successo|correttamente)|"
        r"(?:successo|correttamente).*pubblicat[oa]|inviat[oa]\s*[:\-]|inviat[oa].*(?:successo|correttamente)|creato con successo",
        message,
        re.I,
    ))


def editorial_activities(raw, service_id, limit=8):
    lines = [x for x in raw.splitlines() if x.strip()]
    activities = []
    seen = set()
    for line in reversed(lines):
        date, message = parse_log_line(line)
        if is_error_message(message):
            activity = readable_error(message, date)
        elif is_success_message(message, service_id):
            title = publication_title(message)
            if not title:
                continue
            icon = "🆕" if service_id == "instagram" else "📰"
            activity = f'{icon} {short_date(date)} · "{title[:220]}"'
        else:
            continue
        if activity not in seen:
            seen.add(activity)
            activities.append(activity)
        if len(activities) == limit:
            break
    return activities


def count_successful_publications(raw, service_id):
    publications = set()
    for line in raw.splitlines():
        date, message = parse_log_line(line)
        if not is_success_message(message, service_id):
            continue
        title = publication_title(message)
        if title:
            publications.add((date.strftime("%Y-%m-%d %H:%M") if date else "", title.casefold()))
    return len(publications)


def extract_image_url(message):
    labelled = re.search(r'(?:image|immagine|thumbnail|media_url)["\']?\s*[:=]\s*["\']?(https?://[^\s"\']+)', message, re.I)
    if labelled:
        return labelled.group(1).rstrip(",)}]")
    image = re.search(r'https?://[^\s"\']+\.(?:jpe?g|png|webp|gif)(?:\?[^\s"\']*)?', message, re.I)
    return image.group(0).rstrip(",)}]") if image else None


def rss_activities(raw, limit=8):
    lines = [x for x in raw.splitlines() if x.strip()]
    activities = []
    for index in range(len(lines) - 1, -1, -1):
        date, message = parse_log_line(lines[index])
        if is_error_message(message):
            activities.append({"text": readable_error(message, date), "type": "error"})
        elif is_success_message(message, "rss") or re.search(r"(?:rss|whatsapp|telegram).*(?:inviat|pubblicat)", message, re.I):
            title = publication_title(message)
            if not title:
                continue
            nearby = " ".join(parse_log_line(x)[1] for x in lines[max(0, index - 2):min(len(lines), index + 3)])
            activities.append({
                "text": f'📰 {short_date(date)} · "{title[:220]}"',
                "title": title[:220],
                "image": extract_image_url(nearby),
                "type": "publication",
            })
        if len(activities) == limit:
            break
    return activities


def simplify_logs(raw, service_id=None):
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", x).strip() for x in raw.splitlines() if x.strip()]
    # Le richieste del server web non sono attività editoriali utili.
    lines = [x for x in lines if not re.search(r'\b(?:GET|POST|PUT|DELETE|HEAD|OPTIONS)\s+\S+\s+HTTP/[\d.]+', x)]
    editorial_services = {"instagram", "amazon", "carburanti", "meteo", "oroscopo"}
    if service_id in editorial_services:
        return editorial_activities("\n".join(lines), service_id)

    if service_id == "rss":
        return rss_activities("\n".join(lines))

    important = [x for x in lines if re.search(r"error|errore|failed|exception|success|pubblic|inviat|warning|mail", x, re.I)]
    selected = (important or lines)[-8:]
    formatted = []
    for line in reversed(selected):
        date, message = parse_log_line(line)
        formatted.append(f"{short_date(date)} · {message[-260:]}")
    return formatted


# =====================================================================
# Riepiloghi strutturati per scheda (v2.0.0)
# Ogni riepilogo: {"kpis": [{label, value, tone}], "last": {title, link, image, when, label},
#                  "errors": [{when, text}], "notes": [str], "level": ok|warn|error}
# =====================================================================
ROME = ZoneInfo("Europe/Rome")
WP_URL = os.getenv("WP_URL", "https://www.montagneepaesi.com").rstrip("/")
_cache = {}
_base_cache = {}


def cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        value = fn()
    except Exception:
        value = None
    _cache[key] = (time.time(), value)
    return value


def rome_now():
    return datetime.now(ROME)


def to_rome(value):
    """Converte ISO/date/'YYYY-mm-dd HH:MM:SS' in datetime Europe/Rome."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(ROME) if value.tzinfo else value.replace(tzinfo=ROME)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(ROME) if parsed.tzinfo else parsed.replace(tzinfo=ROME)


def iso(value):
    d = to_rome(value)
    return d.isoformat() if d else None


def is_today(value):
    d = to_rome(value)
    return bool(d and d.date() == rome_now().date())


def short_text(text, n=220):
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:n] + ("…" if len(text) > n else "")


def candidate_bases(service, container):
    port = service.get("port")
    if not port:
        return []
    hosts = []
    if os.getenv("DASHBOARD_HOST"):
        hosts.append(os.getenv("DASHBOARD_HOST"))
    try:
        hosts.append(request.host.split(":", 1)[0])
    except RuntimeError:
        pass
    hosts += ["host.docker.internal", "172.17.0.1"]
    bases = [f"http://{h}:{port}" for h in hosts]
    # Rete interna Docker: IP del container sulla porta interna esposta.
    try:
        nets = (container.attrs.get("NetworkSettings", {}) or {}).get("Networks", {}) or {}
        ports = (container.attrs.get("NetworkSettings", {}) or {}).get("Ports", {}) or {}
        inner = next((k.split("/")[0] for k, v in ports.items() if v and any(str(b.get("HostPort")) == str(port) for b in v)), None)
        for net in nets.values():
            if net.get("IPAddress") and inner:
                bases.append(f"http://{net['IPAddress']}:{inner}")
    except Exception:
        pass
    seen, out = set(), []
    for b in ([_base_cache[service["id"]]] if service["id"] in _base_cache else []) + bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def bot_json(service, container, path, auth=None):
    for base in candidate_bases(service, container):
        try:
            r = requests.get(base + path, timeout=2.5, auth=auth)
            if r.status_code == 401:
                _base_cache[service["id"]] = base
                return {"_unauthorized": True}
            if r.ok:
                _base_cache[service["id"]] = base
                return r.json()
        except (requests.RequestException, ValueError):
            continue
    return None


def read_container_json(container, path):
    try:
        stream, _ = container.get_archive(path)
        raw = b"".join(stream)
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            member = tar.getmembers()[0]
            return json.loads(tar.extractfile(member).read().decode("utf-8"))
    except Exception:
        return None


def wp_get(path, params=None):
    r = requests.get(f"{WP_URL}/wp-json/wp/v2/{path}", params=params, timeout=6,
                     headers={"User-Agent": "MEP-ControlCenter"})
    r.raise_for_status()
    return r.json()


def wp_post_info(post):
    if not post:
        return None
    image = None
    try:
        media = (post.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
        sizes = (media.get("media_details") or {}).get("sizes") or {}
        image = (sizes.get("medium") or sizes.get("thumbnail") or {}).get("source_url") or media.get("source_url")
    except Exception:
        pass
    return {"title": short_text((post.get("title") or {}).get("rendered"), 160), "link": post.get("link"),
            "when": iso(post.get("date")), "image": image}


def wp_by_url(url):
    if not url:
        return None
    slug = [x for x in url.split("?")[0].split("/") if x]
    if not slug:
        return None
    def fetch():
        rows = wp_get("posts", {"slug": slug[-1], "_embed": "wp:featuredmedia", "_fields": "title,link,date,_links,_embedded"})
        return wp_post_info(rows[0]) if rows else None
    return cached(f"wpurl:{url}", 600, fetch)


def wp_search_latest(term):
    def fetch():
        rows = wp_get("posts", {"search": term, "per_page": 1, "orderby": "date", "_embed": "wp:featuredmedia",
                                "_fields": "title,link,date,_links,_embedded"})
        return wp_post_info(rows[0]) if rows else None
    return cached(f"wpsearch:{term}", 600, fetch)


def log_lines(container, tail=1500, since=None):
    kw = {"timestamps": True}
    if since is not None:
        kw["since"] = since
    else:
        kw["tail"] = tail
    raw = container.logs(**kw).decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        if line.strip():
            date, message = parse_log_line(line)
            out.append((date, message))
    return out


def kpi(label, value, tone=""):
    return {"label": label, "value": value, "tone": tone}


# ---------------------------------------------------------------- Instagram
def summary_instagram(service, container):
    st = bot_json(service, container, "/status")
    if st and not st.get("_unauthorized"):
        q = st.get("queue") or []
        used, total = st.get("quota_usage"), st.get("quota_total")
        last = st.get("last_post") or ({"title": st["last_published"]} if st.get("last_published") else None)
        errors = []
        err = str(st.get("last_error") or "")
        if err and not re.match(r"^(SKIP_MEDIA_INVALID|TEMP_|MEDIA_PRECHECK)", err):
            errors.append({"when": None, "text": short_text(err, 200)})
        level = "ok" if st.get("running") else "error"
        if errors and level == "ok":
            level = "warn"
        notes = [st.get("publish_status")] if st.get("publish_status") else []
        if not st.get("running"):
            notes.insert(0, "Bot fermo: premi Avvia nel pannello Instagram")
        return {
            "kpis": [kpi("Pubblicati oggi", st.get("published_today", "—"), "good"),
                     kpi("In coda", len(q)),
                     kpi("Quota Meta", f"{used}/{total}" if used is not None and total else "—",
                         "bad" if used is not None and total and used >= total - 1 else "")],
            "last": dict(last, label="Ultimo post pubblicato") if last else None,
            "queue": [{"title": x.get("title"), "image": x.get("image")} for x in q[:3]],
            "errors": errors, "notes": notes, "level": level, "source": "api",
        }
    # Fallback: log del container
    lines = log_lines(container, tail=1500)
    last, pending = None, None
    for date, msg in lines:
        m = re.search(r"Pubblicazione dalla coda:\s*(.+)$", msg)
        if m:
            pending = m.group(1).strip()
        elif "✅ Pubblicato" in msg and pending:
            last = {"title": pending, "when": iso(date)}
            pending = None
    day_start = rome_now().replace(hour=0, minute=0, second=0, microsecond=0)
    today = count_successful_publications(container.logs(since=day_start, timestamps=True).decode("utf-8", "replace"), "instagram")
    return {"kpis": [kpi("Pubblicati oggi", today, "good")],
            "last": dict(last, label="Ultimo post pubblicato") if last else None,
            "errors": [], "notes": ["Pannello Instagram non raggiungibile: dati ricavati dai log"],
            "level": "warn", "source": "log"}


# ------------------------------------------------- Carburanti / Oroscopo
def summary_daily_article(service, container, default_time, label):
    state = read_container_json(container, "/app/data/state.json") or {}
    if not state:
        return {"kpis": [], "last": None, "errors": [], "level": "warn",
                "notes": ["Stato non leggibile dal container"]}
    now = rome_now()
    last_when = state.get("last_publish")
    ok_today = is_today(last_when)
    publish_time = state.get("publish_time") or default_time
    auto = bool(state.get("automation_enabled"))
    info = wp_by_url(state.get("last_url")) or {}
    last = None
    if last_when or state.get("last_url"):
        when = iso(last_when)
        # carburanti salva solo la data: in quel caso usiamo l'ora del post WordPress se disponibile
        if info.get("when") and len(str(last_when or "")) <= 10:
            when = info["when"]
        last = {"title": info.get("title") or f"{label} del {to_rome(last_when).strftime('%d/%m/%Y') if to_rome(last_when) else ''}",
                "link": state.get("last_url"), "image": info.get("image"), "when": when,
                "label": "Ultimo caricamento riuscito"}
    errors = []
    if state.get("last_error") and not ok_today:
        errors.append({"when": None, "text": short_text(state["last_error"], 220)})
    notes = []
    level = "ok"
    try:
        hh, mm = [int(x) for x in str(publish_time).split(":", 1)]
        due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        due = None
    if not ok_today:
        if errors:
            level = "error"
        elif auto and due and now > due.replace(minute=due.minute) and (now - due).total_seconds() > 1200:
            level = "warn"
            notes.append(f"Oggi non ancora caricato (previsto alle {publish_time})")
        elif auto and due and now <= due:
            notes.append(f"Caricamento di oggi previsto alle {publish_time}")
    if not auto:
        level = "warn" if level == "ok" else level
        notes.append("Automazione disattivata")
    return {"kpis": [kpi("Caricato oggi", "Sì" if ok_today else "No", "good" if ok_today else ("bad" if errors else "")),
                     kpi("Orario", publish_time), kpi("Automazione", "Attiva" if auto else "Spenta", "" if auto else "bad")],
            "last": last, "errors": errors, "notes": notes, "level": level}


# ---------------------------------------------------------------- Meteo
def summary_meteo(service, container):
    health = bot_json(service, container, "/health") or {}
    st = health.get("status") or {}
    post = wp_search_latest("Meteo Lombardia")
    last = None
    if post:
        last = dict(post, label="Ultimo articolo meteo")
    elif st.get("last_post_url"):
        last = {"title": "Articolo meteo", "link": st.get("last_post_url"), "when": iso(st.get("last_run")),
                "label": "Ultimo articolo meteo"}
    ok_today = bool(last and is_today(last.get("when")))
    errors = []
    if st.get("last_error") and not ok_today:
        errors.append({"when": iso(st.get("last_run")), "text": short_text(st["last_error"], 220)})
    notes = []
    if st.get("running"):
        notes.append("Generazione in corso…")
    if st.get("next_run"):
        notes.append(f"Prossimo: {short_text(st['next_run'], 40)}")
    level = "error" if errors else ("ok" if ok_today else "warn")
    if not ok_today and not errors:
        notes.append("Articolo di oggi non ancora trovato sul sito")
    return {"kpis": [kpi("Pubblicato oggi", "Sì" if ok_today else "No", "good" if ok_today else ("bad" if errors else ""))],
            "last": last, "errors": errors, "notes": notes, "level": level}


# ---------------------------------------------------------------- WhatsApp
def summary_whatsapp(service, waha_container, client):
    sender = None
    if client and service.get("sender"):
        sender = get_container(client, {"container": service["sender"]})
    kpis, notes, errors, last = [], [], [], None
    level = "ok" if waha_container and waha_container.status == "running" else "error"
    kpis.append(kpi("WAHA", "Attivo" if level == "ok" else "Fermo", "good" if level == "ok" else "bad"))
    if not sender:
        notes.append("Bot del canale (rssbot_montagne) non trovato")
        return {"kpis": kpis, "last": None, "errors": [], "notes": notes, "level": "warn" if level == "ok" else level}
    lines = log_lines(sender, tail=3000)
    sent, cur, last_check, last_ok_at = [], {}, None, None
    for date, msg in lines:
        if msg.startswith("📰 Nuovo articolo:"):
            cur = {"title": msg.split(":", 1)[1].strip(), "when": None}
        elif msg.startswith("🔗 Link:"):
            cur["link"] = msg.split(":", 1)[1].strip()
        elif msg.startswith("🖼️ Immagine:"):
            img = msg.split(":", 1)[1].strip()
            cur["image"] = img if img.startswith("http") else None
        elif "✅" in msg and "inviat" in msg.lower() and cur.get("title"):
            cur["when"] = iso(date)
            sent.append(cur)
            last_ok_at = date
            cur = {}
        elif msg.startswith("🔍 Recupero RSS"):
            last_check = date
        elif msg.startswith("❌") or msg.startswith("⛔") or msg.startswith("⚠️ Feed"):
            errors.append({"when": iso(date), "text": short_text(msg.lstrip("❌⛔⚠️ "), 200), "_d": date})
    # Solo gli errori successivi all'ultimo invio riuscito
    errors = [e for e in errors if not last_ok_at or (e["_d"] and e["_d"] > last_ok_at)][-3:]
    for e in errors:
        e.pop("_d", None)
    today = sum(1 for x in sent if is_today(x.get("when")))
    kpis.append(kpi("Inviati oggi", today, "good"))
    if sender.status != "running":
        level = "error"
        notes.append("Bot del canale fermo")
    if last_check:
        kpis.append(kpi("Controllo feed", last_check.strftime("%H:%M")))
    if errors and level == "ok":
        level = "warn"
    if sent:
        last = dict(sent[-1], label="Ultimo articolo inviato sul canale")
    return {"kpis": kpis, "last": last, "errors": errors, "notes": notes, "level": level}


# ---------------------------------------------------------------- Eventi
def summary_eventi(service, container):
    user, pwd = os.getenv("EVENTI_WEB_USER", "admin"), os.getenv("EVENTI_WEB_PASSWORD", "")
    st = bot_json(service, container, "/api/status", auth=(user, pwd) if pwd else None)
    if not st:
        return {"kpis": [], "last": None, "errors": [], "level": "warn",
                "notes": ["Pannello eventi non raggiungibile"]}
    if st.get("_unauthorized"):
        return {"kpis": [], "last": None, "errors": [], "level": "warn",
                "notes": ["Pannello eventi protetto da password: imposta EVENTI_WEB_PASSWORD nello stack della dashboard"]}
    run = st.get("last_run") or {}
    live = st.get("live") or {}
    by_status = (st.get("counts") or {}).get("by_status") or {}
    total = int(by_status.get("created", 0)) + int(by_status.get("updated", 0))
    errors, notes = [], []
    if int(run.get("errors") or 0):
        lines = [l.strip() for l in short_text(run.get("report"), 2000).split("•") if l.strip()]
        detail = next((l for l in lines if re.search(r"errore|non raggiungibile|interrott|fallit", l, re.I)), "")
        errors.append({"when": iso(run.get("finished") or run.get("started")),
                       "text": (f"{run.get('errors')} errori" if int(run.get('errors')) > 1 else "1 errore") + " nell'ultimo giro" + (f" · {short_text(detail, 140)}" if detail else "")})
    if live.get("running"):
        notes.append(f"Giro in corso: {live.get('done', 0)}/{live.get('total', 0)} · {live.get('source') or ''}".strip(" ·"))
    elif live.get("next_run"):
        nr = to_rome(live["next_run"])
        notes.append(f"Prossimo giro: {nr.strftime('%d/%m alle %H:%M') if nr else live['next_run']}")
    if st.get("continuous"):
        notes.append("Scansione continua attiva")
    status = run.get("status") or "—"
    level = "error" if status in ("errore", "error", "interrotto") else ("warn" if errors else "ok")
    last = None
    if run:
        when = run.get("finished") or run.get("started")
        last = {"title": f"Giro {run.get('trigger') or ''}: {run.get('created', 0)} nuovi, {run.get('updated', 0)} aggiornati, "
                         f"{run.get('skipped', 0)} scartati".replace("Giro :", "Ultimo giro:"),
                "when": iso(when), "label": f"Ultimo giro · esito {status}"}
    return {"kpis": [kpi("Nuovi", run.get("created", 0), "good"),
                     kpi("Aggiornati", run.get("updated", 0)),
                     kpi("In calendario", total)],
            "last": last, "errors": errors, "notes": notes, "level": level}


def build_summary(service, container, client):
    sid = service["id"]
    if sid == "waha":
        return summary_whatsapp(service, container, client)
    if not container:
        return None
    if sid == "instagram":
        return summary_instagram(service, container)
    if sid == "carburanti":
        return summary_daily_article(service, container, "09:30", "Prezzi carburanti")
    if sid == "oroscopo":
        return summary_daily_article(service, container, "05:30", "Oroscopo")
    if sid == "meteo":
        return summary_meteo(service, container)
    if sid == "eventi":
        return summary_eventi(service, container)
    return None



def service_status(service, client, host):
    item = dict(service)
    container = get_container(client, service)
    with app.test_request_context(base_url=f"http://{host}"):
        url = panel_url(service)
        item.update({"panel_url": url, "checked_at": now_iso(), "state": "unknown", "health": None,
                     "logs": [], "activities": [], "unread": None, "summary": None})
        if container:
            try:
                container.reload()
                item["state"] = container.status
                item["health"] = container.attrs.get("State", {}).get("Health", {}).get("Status")
                item["started_at"] = container.attrs.get("State", {}).get("StartedAt")
                if service["id"] in {"comunicati", "amazon", "foto"}:
                    raw_logs = container.logs(tail=800, timestamps=True).decode("utf-8", errors="replace")
                    item["logs"] = simplify_logs(raw_logs, service["id"])
                    item["activities"] = item["logs"]
            except Exception as exc:
                item["logs"] = [f"Lettura Docker non disponibile: {exc}"]
        try:
            item["summary"] = build_summary(service, container, client)
        except Exception as exc:
            item["summary"] = {"kpis": [], "last": None, "errors": [{"when": None, "text": f"Riepilogo non disponibile: {exc}"}],
                               "notes": [], "level": "warn"}
        item["panel_online"] = check_http(url)
        if service.get("mail_prefix"):
            mailbox = mail_activity(service["mail_prefix"])
            item["unread"] = mailbox["unread"]
            item["activities"] = mailbox["messages"]
            if mailbox.get("error"):
                item["activities"] = [f"🆘 {mailbox['error']}"]
            elif service["id"] == "foto" and mailbox["unread"] is None:
                item["activities"] = ["Configura la casella email nelle Impostazioni"]
    return item


@app.get("/api/status")
def api_status():
    from concurrent.futures import ThreadPoolExecutor
    client = docker_client()
    host = request.host
    with ThreadPoolExecutor(max_workers=len(SERVICES)) as pool:
        result = list(pool.map(lambda sv: service_status(sv, client, host), SERVICES))
    return jsonify({"services": result, "updated_at": now_iso(), "docker_connected": client is not None,
                    "version": APP_VERSION})


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "time": now_iso()})


@app.get("/api/settings")
def get_settings():
    config = load_config()
    mailboxes = {}
    for name in ("comunicati", "foto"):
        saved = config.get("mailboxes", {}).get(name, {})
        mailboxes[name] = {
            "host": saved.get("host", ""),
            "port": saved.get("port", 993),
            "user": saved.get("user", ""),
            "folder": saved.get("folder", "INBOX"),
            "password_saved": bool(saved.get("password")),
        }
    return jsonify({"mailboxes": mailboxes})


@app.post("/api/settings")
def save_settings():
    payload = request.get_json(silent=True) or {}
    current = load_config()
    current.setdefault("mailboxes", {})
    for name in ("comunicati", "foto"):
        incoming = payload.get("mailboxes", {}).get(name, {})
        previous = current["mailboxes"].get(name, {})
        password = incoming.get("password", "")
        current["mailboxes"][name] = {
            "host": str(incoming.get("host", "")).strip(),
            "port": int(incoming.get("port", 993)),
            "user": str(incoming.get("user", "")).strip(),
            "folder": str(incoming.get("folder", "INBOX")).strip() or "INBOX",
            "password": cipher().encrypt(password.encode()).decode() if password else previous.get("password", ""),
        }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    CONFIG_FILE.chmod(0o600)
    return jsonify({"saved": True})


@app.get("/")
def index():
    return render_template("index.html", app_version=APP_VERSION)


@app.get("/impostazioni")
def settings():
    return render_template("settings.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
