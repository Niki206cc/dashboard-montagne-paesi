import os
import re
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
APP_VERSION = "1.2.0"
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
    {"id": "waha", "name": "WhatsApp / WAHA", "icon": "message", "container": "waha", "port": 3000, "description": "Sessione WhatsApp e ultimi invii"},
    {"id": "rss", "name": "RSS Bot", "icon": "rss", "container": "rssbot_montagne", "port": None, "description": "Invio automatico degli articoli"},
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
        r"Pubblicazione dalla coda:\s*[\"“]?(.+?)[\"”]?\s*$",
        r"(?:titolo|articolo)\s*[=:]\s*[\"“]?(.+?)[\"”]?\s*$",
        r"(?:pubblicato|pubblicata|pubblicazione|inviato|inviata|creato con successo)\s*[:\-]\s*[\"“]?(.+?)[\"”]?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            return match.group(1).strip().strip('"“”').replace("“", "").replace("”", "")
    return None


def simplify_logs(raw, service_id=None):
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", x).strip() for x in raw.splitlines() if x.strip()]
    # Le richieste del server web non sono attività editoriali utili.
    lines = [x for x in lines if not re.search(r'\b(?:GET|POST|PUT|DELETE|HEAD|OPTIONS)\s+\S+\s+HTTP/[\d.]+', x)]
    editorial_services = {"instagram", "amazon", "carburanti", "meteo", "oroscopo"}
    if service_id in editorial_services:
        activities = []
        for line in reversed(lines):
            date, message = parse_log_line(line)
            if re.search(r"error|errore|failed|exception|too many actions|user_title=", message, re.I):
                activities.append(readable_error(message, date))
            else:
                title = publication_title(message)
                if title:
                    icon = "🆕" if service_id == "instagram" else "📰"
                    activities.append(f'{icon} {short_date(date)} · "{title[:220]}"')
            if len(activities) == 8:
                break
        return activities

    important = [x for x in lines if re.search(r"error|errore|failed|exception|success|pubblic|inviat|warning|mail", x, re.I)]
    selected = (important or lines)[-8:]
    formatted = []
    for line in reversed(selected):
        date, message = parse_log_line(line)
        formatted.append(f"{short_date(date)} · {message[-260:]}")
    return formatted


@app.get("/api/status")
def api_status():
    client = docker_client()
    result = []
    for service in SERVICES:
        item = dict(service)
        container = get_container(client, service)
        url = panel_url(service)
        item.update({"panel_url": url, "checked_at": now_iso(), "state": "unknown", "health": None, "logs": [], "activities": [], "unread": None})
        if container:
            try:
                container.reload()
                item["state"] = container.status
                item["health"] = container.attrs.get("State", {}).get("Health", {}).get("Status")
                item["started_at"] = container.attrs.get("State", {}).get("StartedAt")
                item["logs"] = simplify_logs(container.logs(tail=300, timestamps=True).decode("utf-8", errors="replace"), service["id"])
                if service["id"] in {"instagram", "carburanti", "meteo", "oroscopo", "amazon"}:
                    item["activities"] = item["logs"]
                    if container.status != "running":
                        stopped = "🛑 Sistema di caricamento Instagram fermo" if service["id"] == "instagram" else f"🛑 Sistema {service['name']} fermo"
                        item["activities"].insert(0, stopped)
            except Exception as exc:
                item["logs"] = [f"Lettura Docker non disponibile: {exc}"]
        elif service["id"] == "instagram" and client:
            item["activities"] = ["🛑 Sistema di caricamento Instagram non trovato"]
        item["panel_online"] = check_http(url)
        if service.get("mail_prefix"):
            mailbox = mail_activity(service["mail_prefix"])
            item["unread"] = mailbox["unread"]
            item["activities"] = mailbox["messages"]
            if mailbox.get("error"):
                item["logs"] = [mailbox["error"], *item["logs"]]
        result.append(item)
    return jsonify({"services": result, "updated_at": now_iso(), "docker_connected": client is not None})


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
