"""
YouTrack Todo — настольный виджет для Windows 11.

Показывает задачи из YouTrack (название, статус, приоритет).
Клик по названию открывает задачу в браузере по умолчанию.
Только стандартная библиотека Python (tkinter, urllib, ctypes).
"""
import base64
import ctypes
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import colorchooser, messagebox

APP = "YouTrackWidget"
CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CFG_PATH = os.path.join(CFG_DIR, "config.json")
SEEN_PATH = os.path.join(CFG_DIR, "seen.json")  # когда последний раз смотрели комментарии задачи
PLAN_PATH = os.path.join(CFG_DIR, "day_plan.json")  # ID задач, отмеченных как «план на день»
URGENT_PATH = os.path.join(CFG_DIR, "urgent.json")  # свои срочные задачи (только в виджете, не в YouTrack)
URGENT_TITLE_MAX, URGENT_TEXT_MAX = 100, 1000
MIN_W, MIN_H = 340, 220  # минимальный размер окна
STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup", "YouTrack Widget.lnk"
)
INSTANCE_PORT = int(os.environ.get("YTW_INSTANCE_PORT") or 47613)  # переменная — только для автотестов

DEFAULTS = {
    "base_url": "",
    "token_enc": "",
    "query": "Assignee: me #Unresolved sort by: Priority",
    "state_field": "State",
    "priority_field": "Priority",
    "refresh_min": 5,
    "max_issues": 100,
    "topmost": False,
    "alpha": 0.97,
    "geometry": "",
    "bg_color": "#1f1f1f",
    "card_color": "#2b2b2b",
    "font_size": 12,
    "tab": "all",          # "all" — все мои задачи, "day" — план на день, "urgent" — свои срочные задачи
    "prio_filter": [],     # выбранные приоритеты; пусто — показывать все
    "due_hot": False,      # фильтр «Горящие сроки»: просрочено, сегодня, завтра
    "due_field": "",       # поле срока; пусто — найти автоматически («Дата окончания», «Due Date»…)
    "sprint_field": "",    # поле спринта; пусто — найти автоматически («Спринт», «Sprint»)
    "sprint_filter": [],   # выбранные спринты; пусто — все
    "tray": True,          # значок в трее; ✕ прячет виджет в трей
    "notify": True,        # уведомления Windows о новых задачах и комментариях
    "hotkey": "Win+Shift+Y",  # показать/спрятать виджет из любого окна; пусто — выключено
}

# --- Палитра (тёмная, в стиле Windows 11); пересчитывается из настроек в apply_theme() ---
BG = "#1f1f1f"
HEADER = "#272727"
HEADER_HOVER = "#333333"
CARD = "#2b2b2b"
CARD_HOVER = "#333333"
TEXT = "#e8e8e8"       # текст на карточках
SUB = "#9d9d9d"
BG_TEXT = "#e8e8e8"    # текст на фоне окна
BG_SUB = "#9d9d9d"
ACCENT = "#4cc2ff"
ERR = "#ff8a80"
BADGE = "#e5484d"
FONT = "Segoe UI"
FS = 12                # размер шрифта названия задачи

# --- Иконки Material Icons (Rounded) — тот же набор, что в MUI @mui/icons-material ---
ICON_FONT_FILE = "MaterialIconsRound-Regular.otf"
ICON_FAMILY = "Material Icons Round"
ICON_OK = False  # шрифт загрузился — иначе показываем запасные символы
ICONS = {  # имя: (код из MaterialIconsRound-Regular.codepoints, запасной символ)
    "refresh": ("", "⟳"), "settings": ("", "⚙"), "push_pin": ("", "📌"),
    "remove": ("", "—"), "close": ("", "✕"),
    "chat_bubble_outline": ("", ""), "chat_bubble": ("", ""),
    "star": ("", "★"), "star_border": ("", "☆"), "wb_sunny": ("", "☀"),
    "filter_list": ("", ""), "arrow_drop_down": ("", "▾"), "search": ("", ""),
    "drag_indicator": ("", "⋮"), "radio_button_unchecked": ("", "○"),
    "check_circle": ("", "✔"), "open_in_new": ("", "↗"),
    "bolt": ("", "⚡"), "add": ("", "+"), "edit": ("", "✎"), "expand_more": ("", "▾"),
    "delete_outline": ("", "🗑"), "delete_sweep": ("", "🗑"),
}


def ic(name: str) -> str:
    glyph, fallback = ICONS[name]
    return glyph if ICON_OK else fallback


def icon_font(size: int):
    return (ICON_FAMILY, size) if ICON_OK else (FONT, max(8, size - 3))


def load_icon_font():
    """Подключить шрифт иконок только для этого процесса (в систему не устанавливается)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "fonts", ICON_FONT_FILE)
    if os.path.exists(path):
        try:
            ctypes.windll.gdi32.AddFontResourceExW(ctypes.c_wchar_p(path), 0x10, 0)  # FR_PRIVATE
        except Exception:
            pass


def check_icon_font(root):
    global ICON_OK
    from tkinter import font as tkfont
    ICON_OK = ICON_FAMILY in tkfont.families(root)


# Окно настроек всегда тёмное, независимо от выбранной темы
DLG_BG, DLG_CARD, DLG_HOVER, DLG_TEXT, DLG_SUB = "#1f1f1f", "#2b2b2b", "#333333", "#e8e8e8", "#9d9d9d"
ACCENT_DLG = "#4cc2ff"


def _lum(color: str) -> float:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _mix(color: str, target: str, k: float) -> str:
    a = [int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(target.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(a, b))


def _valid_color(c) -> bool:
    try:
        return isinstance(c, str) and len(c) == 7 and c[0] == "#" and int(c[1:], 16) >= 0
    except ValueError:
        return False


def apply_theme(bg: str, card: str, font_size: int):
    """Пересчитать палитру из двух цветов: текст подбирается контрастным автоматически."""
    global BG, HEADER, HEADER_HOVER, CARD, CARD_HOVER, TEXT, SUB, BG_TEXT, BG_SUB, ACCENT, FS
    BG = bg if _valid_color(bg) else DEFAULTS["bg_color"]
    CARD = card if _valid_color(card) else DEFAULTS["card_color"]
    bg_dark, card_dark = _lum(BG) < 0.5, _lum(CARD) < 0.5
    HEADER = _mix(BG, "#ffffff", 0.04) if bg_dark else _mix(BG, "#000000", 0.05)
    HEADER_HOVER = _mix(HEADER, "#ffffff", 0.08) if bg_dark else _mix(HEADER, "#000000", 0.08)
    CARD_HOVER = _mix(CARD, "#ffffff", 0.06) if card_dark else _mix(CARD, "#000000", 0.05)
    TEXT, SUB = ("#e8e8e8", "#9d9d9d") if card_dark else ("#1b1b1b", "#5c5c5c")
    BG_TEXT, BG_SUB = ("#e8e8e8", "#9d9d9d") if bg_dark else ("#1b1b1b", "#5c5c5c")
    ACCENT = "#4cc2ff" if card_dark else "#0067c0"
    try:
        FS = max(8, min(20, int(font_size)))
    except (TypeError, ValueError):
        FS = DEFAULTS["font_size"]

FALLBACK_PRIORITY = {
    "show-stopper": "#d32f2f", "критический": "#d32f2f", "critical": "#e53935",
    "major": "#fb8c00", "высокий": "#fb8c00", "high": "#fb8c00",
    "normal": "#1e88e5", "обычный": "#1e88e5", "medium": "#1e88e5",
    "minor": "#757575", "низкий": "#757575", "low": "#757575",
}


# ---------------------------------------------------------------- DPAPI (шифрование токена)
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    inp = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(s: str) -> str:
    return base64.b64encode(_dpapi(s.encode("utf-8"), True)).decode() if s else ""


def unprotect(s: str) -> str:
    if not s:
        return ""
    try:
        return _dpapi(base64.b64decode(s), False).decode("utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------- конфиг
def load_cfg() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict):
    os.makedirs(CFG_DIR, exist_ok=True)
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG_PATH)


# ---------------------------------------------------------------- YouTrack API
class YTError(Exception):
    pass


ISSUE_FIELDS = ("id,idReadable,summary,"
                "customFields(id,name,$type,value(name,fullName,login,archived,released,releaseDate,"
                "color(background,foreground))),"
                "comments(created,deleted,text,author(login,fullName))")


def _post(base_url: str, token: str, path: str, params: dict, body: dict):
    base = base_url.strip().rstrip("/")
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error_description", "")
        except Exception:
            msg = ""
        finally:
            e.close()
        if e.code in (401, 403):
            raise YTError(msg or "Нет прав на изменение задачи")
        raise YTError(msg or f"Ошибка сервера: HTTP {e.code}")
    except urllib.error.URLError as e:
        raise YTError(f"Нет соединения: {e.reason}")


def _field_names(f):
    v = f.get("value")
    vals = v if isinstance(v, list) else [v] if v else []
    return [x.get("name") for x in vals if isinstance(x, dict) and x.get("name")]


def fetch_field_options(base_url: str, token: str, issue: dict, field_name: str, kind: str):
    """Варианты для выпадающего списка поля задачи: {"field", "current", "options": [(подпись, команда, выбрано)]}.
    kind: "state" — разрешённые переходы workflow (или все значения обычного поля), "priority" — значения,
    "sprint" — активные спринты (выбор переносит задачу; клик по текущему — убирает из спринта).
    Команды YouTrack пишутся БЕЗ фигурных скобок: «Приоритет Высокий», «remove Спринт 26.20 add Спринт 26.22»."""
    f = sprint_field_of(issue, field_name) if kind == "sprint" else _find_field(issue, field_name, kind)
    if not f or not f.get("id") or not issue.get("id"):
        raise YTError({"state": "Не найдено поле статуса", "priority": "Не найдено поле приоритета",
                       "sprint": "Не найдено поле спринта"}.get(kind, "Поле не найдено"))
    name, current = f.get("name"), _field_names(f)
    data = _get(base_url, token, f"/api/issues/{issue['id']}/customFields/{f['id']}",
                {"fields": "possibleEvents(id,presentation),"
                           "projectCustomField(bundle(values(name,archived,released,releaseDate)))"})
    values = (((data.get("projectCustomField") or {}).get("bundle") or {}).get("values")) or []
    res = {"field": name, "current": current, "options": []}
    if kind == "state":
        events = data.get("possibleEvents") or []
        if events or f.get("$type") == "StateMachineIssueCustomField":
            res["options"] = [(e.get("presentation") or e["id"], f"{name} {e['id']}", False)
                              for e in events if e.get("id")]
        else:
            res["options"] = [(v["name"], f"{name} {v['name']}", False) for v in values
                              if v.get("name") and not v.get("archived") and v["name"] not in current]
    elif kind == "priority":
        res["options"] = [(v["name"], f"{name} {v['name']}", v["name"] in current) for v in values
                          if v.get("name") and (not v.get("archived") or v["name"] in current)]
    elif kind == "sprint":
        now = time.time() * 1000
        active = [v for v in values if v.get("name") and not v.get("archived") and not v.get("released")
                  and (v.get("releaseDate") or now) >= now]
        active.sort(key=lambda v: v.get("releaseDate") or 0)
        names = [n for n in current] + [v["name"] for v in active if v["name"] not in current]
        drop = " ".join(f"remove {name} {c}" for c in current)
        for n in names:
            if n in current:  # клик по текущему спринту — убрать задачу из него
                res["options"].append((n, f"remove {name} {n}", True))
            else:             # выбор другого спринта — перенос (убрать из текущих, добавить в выбранный)
                res["options"].append((n, f"{drop} add {name} {n}".strip(), False))
    return res


def fetch_state_options(base_url: str, token: str, issue: dict, state_field: str):
    """Варианты смены статуса: [(подпись, команда)] — только разрешённые переходы workflow."""
    return [(label, cmd) for label, cmd, _ in
            fetch_field_options(base_url, token, issue, state_field, "state")["options"]]


def apply_command(base_url: str, token: str, issue: dict, command: str):
    _post(base_url, token, "/api/commands", {}, {"query": command, "issues": [{"id": issue["id"]}]})


def fetch_me(base_url: str, token: str) -> str:
    return _get(base_url, token, "/api/users/me", {"fields": "login"}).get("login") or ""


def fetch_issues(base_url: str, token: str, query: str, top: int, fields: str = ISSUE_FIELDS):
    return _get(base_url, token, "/api/issues", {"query": query, "fields": fields, "$top": str(top)})


def _get(base_url: str, token: str, path: str, params: dict):
    base = base_url.strip().rstrip("/")
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        with e:  # закрыть ответ сервера
            if e.code in (401, 403):
                raise YTError("Доступ запрещён: проверьте токен")
            if e.code == 404:
                raise YTError("Не найдено: проверьте адрес YouTrack")
            if e.code == 400:
                try:
                    msg = json.load(e).get("error_description", "")
                except Exception:
                    msg = ""
                raise YTError(f"Ошибка в запросе: {msg or 'HTTP 400'}")
            raise YTError(f"Ошибка сервера: HTTP {e.code}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, socket.gaierror):
            host = urllib.parse.urlparse(base).hostname
            raise YTError(f"Адрес «{host}» не найден. Проверьте адрес YouTrack "
                          "(скопируйте его из браузера) или подключение к VPN")
        raise YTError(f"Нет соединения: {e.reason}")
    except (TimeoutError, socket.timeout):
        raise YTError("Сервер не ответил вовремя")
    except json.JSONDecodeError:
        raise YTError("Сервер вернул не JSON — проверьте адрес")


def _find_field(issue: dict, name: str, kind: str):
    """Найти поле по имени из настроек; если такого нет — угадать (у всех YouTrack поля названы по-разному)."""
    cfs = issue.get("customFields") or []
    for f in cfs:
        if (f.get("name") or "").strip().lower() == (name or "").strip().lower():
            return f
    for f in cfs:
        n, t = (f.get("name") or "").lower(), f.get("$type") or ""
        if kind == "state" and (t in ("StateMachineIssueCustomField", "StateIssueCustomField")
                                or n in ("state", "status", "статус", "состояние")):
            return f
        if kind == "priority" and ("priorit" in n or "приоритет" in n):
            return f
    return None


def field_value(issue: dict, name: str, kind: str = ""):
    """Вернуть (текст, цвет-словарь) значения кастомного поля."""
    f = _find_field(issue, name, kind)
    if not f:
        return None, None
    v = f.get("value")
    if v is None:
        return None, None
    if isinstance(v, list):
        return ", ".join(x.get("name") or "" for x in v if isinstance(x, dict)) or None, None
    if isinstance(v, dict):
        return v.get("name") or v.get("fullName") or v.get("login"), v.get("color")
    return str(v), None


def live_comments(issue: dict):
    return [c for c in issue.get("comments") or [] if not c.get("deleted")]


# ---------------------------------------------------------------- спринт
def sprint_field_of(issue: dict, sprint_field: str):
    """Поле спринта задачи: по имени из настроек или по смыслу
    (поле-версия со словом «спринт»/«sprint» — с ним синхронизируются Agile-доски)."""
    cfs = issue.get("customFields") or []
    f = None
    if sprint_field:
        f = next((x for x in cfs if (x.get("name") or "").lower() == sprint_field.lower()), None)
    if f is None:
        f = next((x for x in cfs if "Version" in (x.get("$type") or "")
                  and any(h in (x.get("name") or "").lower() for h in ("спринт", "sprint"))), None)
    return f


def sprints_of(issue: dict, sprint_field: str):
    """Спринты задачи: [(название, активный?)]."""
    f = sprint_field_of(issue, sprint_field)
    v = f.get("value") if f else None
    if not v:
        return []
    now = time.time() * 1000
    res = []
    for s in (v if isinstance(v, list) else [v]):
        if isinstance(s, dict) and s.get("name"):
            active = not s.get("archived") and not s.get("released") and (s.get("releaseDate") or now) >= now
            res.append((s["name"], active, s.get("releaseDate") or 0))
    res.sort(key=lambda s: (not s[1], -s[2]))  # активный и самый свежий — первым
    return [(n, a) for n, a, _ in res]


# ---------------------------------------------------------------- сроки
DUE_HINTS = ("due", "deadline", "срок", "дедлайн", "окончан")
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def due_date(issue: dict, due_field: str):
    """Дата срока задачи (datetime.date) или None. Поле ищется по имени из настроек или по смыслу."""
    import datetime as dt
    cfs = issue.get("customFields") or []
    f = None
    if due_field:
        f = next((x for x in cfs if (x.get("name") or "").lower() == due_field.lower()), None)
    if f is None:
        f = next((x for x in cfs if x.get("$type") in ("DateIssueCustomField", "DateTimeIssueCustomField")
                  and any(h in (x.get("name") or "").lower() for h in DUE_HINTS)), None)
    v = f.get("value") if f else None
    if not isinstance(v, (int, float)):
        return None
    return dt.datetime.fromtimestamp(v / 1000).date()


def due_info(d):
    """(текст, фон, цвет текста, «горит»?) для плашки срока."""
    import datetime as dt
    if d is None:
        return None
    today = dt.date.today()
    delta = (d - today).days
    label = f"{WEEKDAYS[d.weekday()]}, {d:%d.%m}" + ("" if d.year == today.year else f".{d:%y}")
    if delta < 0:
        return f"просрочено · {label}", "#d32f2f", "#ffffff", True
    if delta == 0:
        return "сегодня", "#ef6c00", "#ffffff", True
    if delta == 1:
        return "завтра", "#f9a825", "#1b1b1b", True
    return f"до {label}", None, None, False


def last_comment_time(issue: dict) -> int:
    return max((c.get("created") or 0 for c in live_comments(issue)), default=0)


def load_seen() -> dict:
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def load_plan():
    """План на день: (ID задач в нужном порядке, ID выполненных сегодня).
    Отметки «выполнено» действуют до конца дня; старый формат файла (просто список) тоже читается."""
    try:
        with open(PLAN_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], set()
    if isinstance(data, list):
        return [x for x in data if isinstance(x, str)], set()
    ids = [x for x in data.get("ids", []) if isinstance(x, str)]
    done = set(data.get("done", [])) if data.get("done_date") == _today() else set()
    return ids, done


def save_plan(plan: list, done=()):
    os.makedirs(CFG_DIR, exist_ok=True)
    tmp = PLAN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ids": plan, "done_date": _today(), "done": sorted(done)}, f, ensure_ascii=False)
    os.replace(tmp, PLAN_PATH)


def load_urgent() -> list:
    """Срочные задачи: [{id, title, text, created, done, done_at}]. Длина полей ограничивается и при чтении."""
    try:
        with open(URGENT_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    items = []
    for x in data if isinstance(data, list) else []:
        if isinstance(x, dict) and str(x.get("title") or "").strip() and x.get("id"):
            items.append({"id": str(x["id"]), "title": str(x["title"]).strip()[:URGENT_TITLE_MAX],
                          "text": str(x.get("text") or "")[:URGENT_TEXT_MAX],
                          "created": int(x.get("created") or 0), "done": bool(x.get("done")),
                          "done_at": int(x.get("done_at") or 0)})
    return items


def save_urgent(items: list):
    os.makedirs(CFG_DIR, exist_ok=True)
    tmp = URGENT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, URGENT_PATH)


def save_seen(seen: dict):
    os.makedirs(CFG_DIR, exist_ok=True)
    tmp = SEEN_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.replace(tmp, SEEN_PATH)


def _hex(c: str):
    c = (c or "").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return None
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def chip_colors(text, color):
    """Цвета плашки: из YouTrack, но слишком светлые фоны заменяем нейтральным."""
    neutral = ("#3a3a3a", "#d6d6d6") if _lum(CARD) < 0.5 else ("#e3e5e8", "#2b2b2b")
    if color:
        rgb = _hex(color.get("background"))
        if rgb:
            lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
            if lum < 0.88:
                fg = color.get("foreground") or ("#ffffff" if lum < 0.6 else "#111111")
                return "#" + "".join(f"{x:02x}" for x in rgb), fg
    if text and text.lower() in FALLBACK_PRIORITY:
        return FALLBACK_PRIORITY[text.lower()], "#ffffff"
    return neutral


# ---------------------------------------------------------------- уведомления Windows
# Windows показывает этот идентификатор в заголовке уведомления как название приложения
AUMID = "YouTrack Widget"
TOAST_ICON = os.path.join(CFG_DIR, "app.png")


def register_toast_app():
    """Кладёт иконку для уведомлений в папку настроек (из exe её путь каждый раз временный)."""
    try:
        import shutil
        src = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "app.png")
        os.makedirs(CFG_DIR, exist_ok=True)
        if os.path.exists(src):
            shutil.copy2(src, TOAST_ICON)
    except Exception:
        pass


_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
    "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime]|Out-Null;"
    "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml($env:YTW_XML);"
    "$t=[Windows.UI.Notifications.ToastNotification]::new($x);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:YTW_AUMID).Show($t)"
)


def show_toast(title: str, body: str, url: str = "", extra: str = ""):
    """Всплывающее уведомление Windows; клик открывает url в браузере по умолчанию."""
    from xml.sax.saxutils import escape, quoteattr
    launch = f' activationType="protocol" launch={quoteattr(url)}' if url else ""
    lines = "".join(f"<text>{escape(t)}</text>" for t in (title, body, extra) if t)
    if os.path.exists(TOAST_ICON):  # иконка приложения слева в уведомлении
        lines += f"<image placement=\"appLogoOverride\" src={quoteattr('file:///' + TOAST_ICON.replace(os.sep, '/'))}/>"
    xml = (f"<toast{launch}><visual><binding template=\"ToastGeneric\">{lines}"
           f"</binding></visual><audio src=\"ms-winsoundevent:Notification.Default\"/></toast>")

    def run():
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                           env=dict(os.environ, YTW_XML=xml, YTW_AUMID=AUMID),
                           creationflags=0x08000000, timeout=30, capture_output=True)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def clean_markdown(text: str, limit: int) -> str:
    """Убрать разметку YouTrack (жирный, ссылки, код, цитаты), сохранив абзацы; обрезать до limit."""
    import re
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "[картинка]", text or "")  # картинки — до ссылок
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)              # [текст](ссылка) → текст
    text = re.sub(r"```.*?```", "[код]", text, flags=re.S)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def fetch_description(base_url: str, token: str, issue: dict) -> str:
    return _get(base_url, token, f"/api/issues/{issue['id']}", {"fields": "description"}).get("description") or ""


def comment_snippet(c: dict, limit: int = 140) -> str:
    import re
    text = re.sub(r"[*_`>#]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", c.get("text") or "")
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


# ---------------------------------------------------------------- глобальная горячая клавиша
_MODS = {"ctrl": 0x2, "control": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}


def parse_hotkey(spec: str):
    """'Win+Shift+Y' → (модификаторы, код клавиши) или None, если сочетание записано неверно."""
    parts = [p.strip().lower() for p in (spec or "").replace(" ", "").split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods |= _MODS[p]
    key = parts[-1].upper()
    if len(key) == 1 and (key.isalpha() and key.isascii() or key.isdigit()):
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        vk = 0x70 + int(key[1:]) - 1
    else:
        return None
    return mods, vk


def start_hotkey(spec: str, q: queue.Queue):
    """Регистрирует сочетание в отдельном потоке. Возвращает (id потока, удалось ли)."""
    from ctypes import wintypes
    parsed = parse_hotkey(spec)
    if not parsed:
        return None, False
    mods, vk = parsed
    result, ready = {}, threading.Event()

    def loop():
        user32 = ctypes.windll.user32
        result["tid"] = ctypes.windll.kernel32.GetCurrentThreadId()
        result["ok"] = bool(user32.RegisterHotKey(None, 1, mods | 0x4000, vk))  # 0x4000 = MOD_NOREPEAT
        ready.set()
        if not result["ok"]:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                q.put(("hotkey", None))
        user32.UnregisterHotKey(None, 1)

    threading.Thread(target=loop, daemon=True).start()
    ready.wait(2)
    ok = result.get("ok", False)
    return (result.get("tid") if ok else None), ok


def stop_hotkey(tid):
    if tid:
        ctypes.windll.user32.PostThreadMessageW(tid, 0x0012, 0, 0)  # WM_QUIT


# ---------------------------------------------------------------- значок в трее
_DIGITS = {  # пиксельный шрифт 3×5 для числа на значке
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"), "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"), "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "111"),
}


def tray_pixels(size: int, count: int) -> bytes:
    """Иконка приложения + красный кружок с числом новых комментариев. Возвращает BGRA сверху вниз."""
    try:
        import make_icon
        rows = [bytearray(r) for r in make_icon.render_rgba(size)]
    except Exception:  # запасной вариант — синий квадрат
        rows = [bytearray(b"\x1e\x88\xe5\xff" * size) for _ in range(size)]

    def put(x, y, rgba):
        if 0 <= x < size and 0 <= y < size:
            rows[y][x * 4:x * 4 + 4] = bytes(rgba)

    if count:
        label = str(min(count, 99))
        scale = max(1, size // 16)
        tw, th = (len(label) * 4 - 1) * scale, 5 * scale
        # компактная «таблетка» в правом нижнем углу — по размеру числа, чтобы иконка оставалась узнаваемой
        h = th + 2 * scale
        wdt = max(h, tw + 2 * scale + (scale if len(label) > 1 else 0))
        left, top = size - wdt, size - h
        rad = h / 2
        for y in range(top - 1, size):
            for x in range(left - 1, size):
                cx = min(max(x + .5, left + rad), size - rad)
                d = ((x + .5 - cx) ** 2 + (y + .5 - (top + rad)) ** 2) ** .5
                if d <= rad + 0.9:  # белая обводка, внутри красный
                    put(x, y, (229, 72, 77, 255) if d <= rad else (255, 255, 255, 255))
        x0, y0 = left + (wdt - tw + 1) // 2, top + (h - th) // 2
        for n, ch in enumerate(label):
            for gy, line in enumerate(_DIGITS[ch]):
                for gx, bit in enumerate(line):
                    if bit == "1":
                        for sy in range(scale):
                            for sx in range(scale):
                                put(x0 + (n * 4 + gx) * scale + sx, y0 + gy * scale + sy, (255, 255, 255, 255))
    out = bytearray()
    for row in rows:  # RGBA → BGRA
        for i in range(0, len(row), 4):
            out += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
    return bytes(out)


class Tray:
    """Значок в области уведомлений: число новых комментариев, клик — показать/спрятать, правый клик — меню.
    Работает в своём потоке со скрытым окном Windows; с Tk общается через очередь q."""
    WM_TRAY, WM_UPDATE, WM_QUIT_TRAY = 0x8001, 0x8002, 0x8003
    MENU = ((1, "Показать / спрятать"), (2, "Обновить"), (3, "Отметить всё прочитанным"), (0, None), (9, "Выход"))

    def __init__(self, q: queue.Queue):
        self.q = q
        self.count, self.tip = 0, "YouTrack Widget"
        self.hwnd = None
        self._icon = None
        self._ready = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(3)
        return self.hwnd is not None

    def update(self, count: int, tip: str):
        self.count, self.tip = count, tip[:127]
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, self.WM_UPDATE, 0, 0)

    def stop(self):
        if self.hwnd:
            ctypes.windll.user32.PostMessageW(self.hwnd, self.WM_QUIT_TRAY, 0, 0)
            self.hwnd = None

    # --- всё ниже выполняется в потоке трея
    def _run(self):
        from ctypes import wintypes as wt
        user32, shell32, gdi32, kernel32 = (ctypes.windll.user32, ctypes.windll.shell32,
                                            ctypes.windll.gdi32, ctypes.windll.kernel32)
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.restype = wt.HWND
        user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
        user32.CreateIconIndirect.restype = wt.HICON
        user32.CreatePopupMenu.restype = wt.HMENU
        user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
        user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        gdi32.CreateDIBSection.restype = wt.HBITMAP
        gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT, ctypes.POINTER(ctypes.c_void_p),
                                           wt.HANDLE, wt.DWORD]
        gdi32.CreateBitmap.restype = wt.HBITMAP
        gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wt.UINT, wt.UINT, ctypes.c_void_p]
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        user32.CreateIconIndirect.argtypes = [ctypes.c_void_p]
        user32.DestroyIcon.argtypes = [wt.HICON]
        user32.DestroyMenu.argtypes = [wt.HMENU]
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.GetMessageW.argtypes = [ctypes.c_void_p, wt.HWND, wt.UINT, wt.UINT]
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.c_void_p]
        kernel32.GetModuleHandleW.restype = wt.HMODULE

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                        ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                        ("lpszClassName", wt.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("a", wt.DWORD), ("b", wt.WORD), ("c", wt.WORD), ("d", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT), ("uFlags", wt.UINT),
                        ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON), ("szTip", wt.WCHAR * 128),
                        ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD), ("szInfo", wt.WCHAR * 256),
                        ("uVersion", wt.UINT), ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD),
                        ("guidItem", GUID), ("hBalloonIcon", wt.HICON)]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                        ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        class ICONINFO(ctypes.Structure):
            _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                        ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]

        size = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON — размер значка трея с учётом масштаба

        def make_icon(count):
            px = tray_pixels(size, count)
            bih = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            color = gdi32.CreateDIBSection(None, ctypes.byref(bih), 0, ctypes.byref(bits), None, 0)
            ctypes.memmove(bits, px, len(px))
            mask = gdi32.CreateBitmap(size, size, 1, 1, None)
            info = ICONINFO(True, 0, 0, mask, color)
            icon = user32.CreateIconIndirect(ctypes.byref(info))
            gdi32.DeleteObject(color)
            gdi32.DeleteObject(mask)
            return icon

        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.uID = 1
        nid.uFlags = 0x1 | 0x2 | 0x4  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.WM_TRAY

        def apply(action):
            old = self._icon
            self._icon = make_icon(self.count)
            nid.hIcon = self._icon
            nid.szTip = self.tip
            ok = shell32.Shell_NotifyIconW(action, ctypes.byref(nid))
            if action == 0:
                self.added = bool(ok)
            if old:
                user32.DestroyIcon(old)

        def show_menu(hwnd):
            menu = user32.CreatePopupMenu()
            for cmd, text in self.MENU:
                user32.AppendMenuW(menu, 0x800 if text is None else 0, cmd, text)  # MF_SEPARATOR / MF_STRING
            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)  # иначе меню не закроется по клику мимо
            cmd = user32.TrackPopupMenu(menu, 0x100 | 0x2 | 0x20, pt.x, pt.y, 0, hwnd, None)
            user32.PostMessageW(hwnd, 0, 0, 0)
            user32.DestroyMenu(menu)
            if cmd:
                self.q.put(("tray", cmd))

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.WM_TRAY:
                ev = lparam & 0xFFFF
                if ev == 0x202:      # WM_LBUTTONUP
                    self.q.put(("tray", 1))
                elif ev == 0x205:    # WM_RBUTTONUP
                    show_menu(hwnd)
                return 0
            if msg == self.WM_UPDATE:
                apply(1)  # NIM_MODIFY
                return 0
            if msg == taskbar_created:  # Проводник перезапустился — значок нужно добавить заново
                apply(0)
                return 0
            if msg == self.WM_QUIT_TRAY:
                shell32.Shell_NotifyIconW(2, ctypes.byref(nid))  # NIM_DELETE
                if self._icon:
                    user32.DestroyIcon(self._icon)
                user32.DestroyWindow(hwnd)
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = WNDPROC(wndproc)  # держим ссылку, иначе сборщик мусора удалит callback
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=hinst, lpszClassName="YouTrackWidgetTray")
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, "YouTrackWidgetTray", "YouTrack Widget tray", 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            self._ready.set()
            return
        nid.hWnd = hwnd
        apply(0)  # NIM_ADD
        self.hwnd = hwnd
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# ---------------------------------------------------------------- автозапуск
def pythonw_path():
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def set_autostart(enable: bool):
    if not enable:
        if os.path.exists(STARTUP_LNK):
            os.remove(STARTUP_LNK)
        return
    if getattr(sys, "frozen", False):  # собранный .exe
        target, args = sys.executable, ""
    else:
        target, args = pythonw_path(), f'"{os.path.abspath(__file__)}"'
    env = dict(os.environ, YTW_LNK=STARTUP_LNK, YTW_TARGET=target, YTW_ARGS=args,
               YTW_DIR=os.path.dirname(os.path.abspath(target if args == "" else __file__)))
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:YTW_LNK);"
          "$s.TargetPath=$env:YTW_TARGET;$s.Arguments=$env:YTW_ARGS;"
          "$s.WorkingDirectory=$env:YTW_DIR;$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env=env, check=True, creationflags=0x08000000)


# ---------------------------------------------------------------- один экземпляр
def acquire_single_instance(q: queue.Queue) -> bool:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", INSTANCE_PORT))
    except OSError:
        srv.close()
        try:
            with socket.create_connection(("127.0.0.1", INSTANCE_PORT), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass
        return False
    srv.listen(2)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    if conn.recv(16) == b"show":
                        q.put(("show", None))
            except OSError:
                break

    threading.Thread(target=loop, daemon=True).start()
    return True


# ---------------------------------------------------------------- UI
class Widget(tk.Tk):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.cfg = load_cfg()
        self.issues = []
        self.loading = False
        self.refresh_job = None
        self.collapsed = False
        self._data_sig = None
        self.last_ok = None
        self.seen = load_seen()
        self.plan, self.plan_done = load_plan()
        self.urgent = load_urgent()
        self._done_date = _today()
        self.expanded = set()      # карточки, раскрытые для быстрого просмотра
        self.details = {}          # idReadable -> описание задачи (или YTError / None = загружается)
        self._drag = None          # состояние перетаскивания карточки в плане на день
        self._hotkey_tid = None
        self.tray = None
        self._notified = None   # idReadable -> время последнего комментария, о котором уже уведомили
        self._known_ids = None  # задачи из прошлой загрузки (чтобы заметить новые назначения)
        self.me_login = ""

        self.title("YouTrack Todo")
        self.configure(bg=BG)
        ico = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "app.ico")
        if os.path.exists(ico):
            try:
                self.iconbitmap(default=ico)
            except tk.TclError:
                pass
        self.overrideredirect(True)
        self.attributes("-topmost", bool(self.cfg["topmost"]))
        self.attributes("-alpha", float(self.cfg["alpha"]))
        self.minsize(MIN_W, 44)
        geo = self.cfg.get("geometry") or f"400x560+{self.winfo_screenwidth() - 440}+80"
        self.geometry(geo)

        apply_theme(self.cfg["bg_color"], self.cfg["card_color"], self.cfg["font_size"])
        check_icon_font(self)
        self.configure(bg=BG)
        self._build()
        self.after(50, self._win11_style)
        self.after(200, self._poll_queue)
        self.after(300, self.setup_hotkey)
        self.after(350, self.setup_tray)

        if not self.cfg["base_url"] or not unprotect(self.cfg["token_enc"]):
            self._show_message("Нажмите ⚙, чтобы подключить YouTrack")
            self.after(400, self.open_settings)
        else:
            self.refresh()
        if self.cfg.get("tab") == "urgent":  # срочные задачи видны сразу, не дожидаясь YouTrack
            self.after(100, self.render)

    # ----- оформление окна
    def _win11_style(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            ex = (ex & ~0x00000080) | 0x00040000  # убрать TOOLWINDOW, добавить APPWINDOW → иконка на панели задач
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex)
            pref = ctypes.c_int(2)  # DWMWCP_ROUND — скруглённые углы Win11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            self.withdraw()
            self.after(10, self.deiconify)
        except Exception:
            pass

    def _build(self):
        self.search_box = None  # при перестройке окна старое поле поиска уже удалено
        # Заголовок
        self.header = tk.Frame(self, bg=HEADER, height=40)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        self.title_lbl = tk.Label(self.header, text="YouTrack", bg=HEADER, fg=BG_TEXT, font=(FONT, 11, "bold"))
        self.title_lbl.pack(side="left", padx=(12, 4))
        self.count_icon = tk.Label(self.header, text=ic("chat_bubble") if ICON_OK else "💬", bg=HEADER,
                                   fg=BADGE, font=icon_font(13))
        self.count_lbl = tk.Label(self.header, text="", bg=HEADER, fg=BG_SUB, font=(FONT, 9))
        self.count_lbl.pack(side="left")

        for name, cmd in (("close", self.on_close), ("remove", self.toggle_collapse),
                          ("push_pin", self.toggle_topmost), ("settings", self.open_settings),
                          ("refresh", self.refresh)):
            b = tk.Label(self.header, text=ic(name), bg=HEADER, fg=BG_SUB, font=icon_font(16),
                         padx=5, pady=2, cursor="hand2")
            b.pack(side="right", padx=1)
            b.bind("<Button-1>", lambda e, c=cmd: c())
            b.bind("<Enter>", lambda e, w=b: w.configure(fg=BG_TEXT, bg=HEADER_HOVER))
            b.bind("<Leave>", lambda e, w=b: self._btn_leave(w))
            if name == "push_pin":
                self.pin_btn = b
            elif name == "remove":
                self.collapse_btn = b
        self._btn_leave(self.pin_btn)
        self._sync_collapse_btn()

        # шапка: перетаскивание окна. Двойной клик больше НЕ сворачивает список — это легко сделать
        # случайно при перетаскивании. А обычный клик по свёрнутой шапке — разворачивает.
        for w in (self.header, self.title_lbl, self.count_lbl):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)
            w.bind("<ButtonRelease-1>", self._drag_end)

        # Тело
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)

        # Вкладки
        tabs = tk.Frame(self.body, bg=BG)
        tabs.pack(fill="x", padx=10, pady=(6, 0))
        self.tab_widgets = {}
        for key in ("all", "day", "urgent"):
            cell = tk.Frame(tabs, bg=BG, cursor="hand2")
            cell.pack(side="left", padx=(0, 2))
            top = tk.Frame(cell, bg=BG, cursor="hand2")
            top.pack(padx=6, pady=3)
            icon_lbl = None
            if key in ("day", "urgent"):
                icon_lbl = tk.Label(top, text=ic("wb_sunny" if key == "day" else "bolt"), bg=BG,
                                    font=icon_font(FS + 1), cursor="hand2")
                icon_lbl.pack(side="left", padx=(0, 3))
            lbl = tk.Label(top, text="", bg=BG, font=(FONT, max(9, FS - 2), "bold"), cursor="hand2")
            lbl.pack(side="left")
            bar = tk.Frame(cell, height=2, bg=BG)
            bar.pack(fill="x")
            for w in (cell, top, lbl, icon_lbl):
                if w is not None:
                    w.bind("<Button-1>", lambda e, k=key: self.set_tab(k))
            self.tab_widgets[key] = (lbl, bar, icon_lbl)
        # «Очистить план» — компактный значок, чтобы ряд вкладок помещался и в узком окне
        self.clear_plan_btn = tk.Label(tabs, text=ic("delete_sweep"), bg=BG, fg=BG_SUB, font=icon_font(FS + 3),
                                       cursor="hand2")
        self.clear_plan_btn.bind("<Button-1>", lambda e: self.clear_plan())
        self.clear_plan_btn.bind("<Enter>", lambda e: self.status_lbl.configure(text="Очистить план на день",
                                                                                fg=BG_SUB))
        self._short_tabs = self.winfo_width() < 470
        self._update_tabs()

        search_wrap = tk.Frame(self.body, bg=BG)
        search_wrap.pack(fill="x", padx=10, pady=(6, 4))
        self.search_var = tk.StringVar()
        # Фильтр по приоритету: кнопка [filter_list  Приоритет: все  arrow_drop_down] + меню с галочками
        self.prio_btn = tk.Frame(search_wrap, bg=CARD, cursor="hand2")
        self.prio_btn.pack(side="right", fill="y", padx=(6, 0))
        self.prio_icon = tk.Label(self.prio_btn, text=ic("filter_list"), bg=CARD, font=icon_font(FS + 2),
                                  cursor="hand2")
        self.prio_icon.pack(side="left", padx=(8, 4))
        self.prio_text = tk.Label(self.prio_btn, text="", bg=CARD, font=(FONT, max(9, FS - 2)), cursor="hand2")
        self.prio_text.pack(side="left")
        self.prio_arrow = tk.Label(self.prio_btn, text=ic("arrow_drop_down"), bg=CARD, font=icon_font(FS + 4),
                                   cursor="hand2")
        self.prio_arrow.pack(side="left", padx=(0, 4))
        self.prio_menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
                                 activeforeground=TEXT, selectcolor=TEXT, font=(FONT, max(9, FS - 2)))

        def open_prio_menu(_=None):
            self.prio_menu.tk_popup(self.prio_btn.winfo_rootx(),
                                    self.prio_btn.winfo_rooty() + self.prio_btn.winfo_height())

        for w in (self.prio_btn, self.prio_icon, self.prio_text, self.prio_arrow):
            w.bind("<Button-1>", open_prio_menu)
            w.bind("<Enter>", lambda e: self._prio_hover(True))
            w.bind("<Leave>", lambda e: self._prio_hover(False))
        self.prio_vars = {}
        self._update_prio_button()

        # На «Срочных» вместо фильтра — кнопки «удалить выполненные» и «+ Новая»
        self.urgent_btns = tk.Frame(search_wrap, bg=BG)
        add_bg = ACCENT_DLG if _lum(BG) < 0.5 else "#0067c0"
        add_fg = "#0b1a24" if _lum(BG) < 0.5 else "#ffffff"
        add = tk.Frame(self.urgent_btns, bg=add_bg, cursor="hand2")
        add.pack(side="right", fill="y")
        add_parts = [add, tk.Label(add, text=ic("add"), bg=add_bg, fg=add_fg, font=icon_font(FS + 2), cursor="hand2"),
                     tk.Label(add, text="Новая", bg=add_bg, fg=add_fg, font=(FONT, max(9, FS - 2), "bold"),
                              cursor="hand2")]
        add_parts[1].pack(side="left", padx=(6, 0))
        add_parts[2].pack(side="left", padx=(2, 10))
        for wdg in add_parts:
            wdg.bind("<Button-1>", lambda e: self.open_urgent_editor())
        self.sweep_btn = tk.Label(self.urgent_btns, text=ic("delete_sweep"), bg=CARD, fg=SUB,
                                  font=icon_font(FS + 3), cursor="hand2", padx=6)
        self.sweep_btn.bind("<Button-1>", lambda e: self.clear_done_urgent())
        self.sweep_btn.bind("<Enter>", lambda e: self.status_lbl.configure(text="Удалить выполненные срочные задачи",
                                                                           fg=BG_SUB))
        if self.cfg.get("tab") == "urgent":
            self.prio_btn.pack_forget()

        # Поле поиска с иконкой search
        search_box = tk.Frame(search_wrap, bg=CARD)
        search_box.pack(side="left", fill="x", expand=True)
        self.search_box = search_box
        if ICON_OK:
            tk.Label(search_box, text=ic("search"), bg=CARD, fg=SUB, font=icon_font(FS + 2)).pack(
                side="left", padx=(8, 0))
        self.search = tk.Entry(search_box, textvariable=self.search_var, bg=CARD, fg=TEXT,
                               insertbackground=TEXT, relief="flat", font=(FONT, FS - 1))
        self.search.pack(side="left", fill="x", expand=True, ipady=5, ipadx=6)
        self._placeholder(self.search, "Поиск…")
        self.search.bind("<Return>", lambda e: self._apply_filter_now())
        self.search.bind("<Escape>", lambda e: (self.search_var.set(""), self._apply_filter_now()))

        list_wrap = tk.Frame(self.body, bg=BG)
        list_wrap.pack(fill="both", expand=True, padx=(10, 2))
        # Весь список рисуется на ОДНОМ холсте (карточка = фигуры и текст, а не десяток виджетов):
        # так ресайз и прокрутка не пересчитывают сотни вложенных фреймов.
        self.canvas = tk.Canvas(list_wrap, bg=BG, highlightthickness=0, bd=0, yscrollincrement=1)
        self.scroll = tk.Scrollbar(list_wrap, orient="vertical", command=self.canvas.yview, width=10)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        # мышь обрабатывается ОДНИМ набором обработчиков на весь холст (а не на каждую карточку)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_rclick)
        self._make_fonts()
        # подписываемся на поиск только когда список уже создан;
        # фильтр срабатывает через паузу после ввода, а не на каждый символ
        self._search_job = None
        self._layout_job = None
        self._layout_width = 0
        self.cards = []      # [(issue, данные карточки)] после вкладки, до поиска/фильтра
        self.visible = []    # карточки, прошедшие поиск и фильтр
        self.card_items = {} # idReadable -> {"star": id, "badge": [ids], "i": индекс}
        self._hover = None
        self.search_var.trace_add("write", lambda *a: self._schedule_filter())

        footer = tk.Frame(self.body, bg=BG)
        footer.pack(fill="x", side="bottom")
        self.status_lbl = tk.Label(footer, text="", bg=BG, fg=BG_SUB, font=(FONT, 8), anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True, padx=12, pady=4)
        grip = tk.Label(footer, text="◢", bg=BG, fg=BG_SUB, font=(FONT, 9), cursor="size_nw_se")
        grip.pack(side="right", padx=(0, 3))
        grip.bind("<ButtonPress-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)
        grip.bind("<ButtonRelease-1>", lambda e: self._save_geometry())

        # Контекстное меню задачи
        self.menu = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
                            activeforeground=TEXT, bd=0)
        self.menu_issue = None
        self.menu.add_command(label="Открыть в браузере", command=lambda: self.open_issue(self.menu_issue))
        self.menu.add_command(label="Добавить в план на день", command=lambda: self.toggle_plan(self.menu_issue))
        self.menu_plan_index = 1
        # подменю статусов заполняется при открытии: только переходы, разрешённые для этой задачи
        self.state_menu = tk.Menu(self.menu, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
                                  activeforeground=TEXT, bd=0, postcommand=self._fill_state_menu)
        self.menu.add_cascade(label="Сменить статус", menu=self.state_menu)
        self._state_cache = {}
        self.menu.add_command(label="Отметить комментарии прочитанными",
                              command=lambda: self.mark_read(self.menu_issue))
        self.menu.add_command(label="Отметить все задачи прочитанными", command=self.mark_all_read)
        self.menu.add_separator()
        self.menu.add_command(label="Копировать ID", command=lambda: self._copy(self.menu_issue["idReadable"]))
        self.menu.add_command(label="Копировать ссылку", command=lambda: self._copy(self.issue_url(self.menu_issue)))

    def _make_fonts(self):
        """Шрифты как объекты: размеры текста меряем без создания виджетов."""
        from tkinter import font as tkfont
        small = max(8, FS - 2)
        self.f_title = tkfont.Font(self, family=FONT, size=FS)
        self.f_title_ul = tkfont.Font(self, family=FONT, size=FS, underline=True)
        self.f_small = tkfont.Font(self, family=FONT, size=small)
        self.f_chip = tkfont.Font(self, family=FONT, size=small, weight="bold")
        self.f_badge = tkfont.Font(self, family=FONT, size=7, weight="bold")
        self.f_icon = tkfont.Font(self, family=ICON_FAMILY if ICON_OK else FONT, size=18 if ICON_OK else 13)
        self.f_star = tkfont.Font(self, family=ICON_FAMILY if ICON_OK else FONT, size=FS + 6 if ICON_OK else FS + 2)
        self.f_msg = tkfont.Font(self, family=FONT, size=FS - 1)
        self.f_title_done = tkfont.Font(self, family=FONT, size=FS, overstrike=True)
        self.f_body = tkfont.Font(self, family=FONT, size=max(9, FS - 1))
        self.f_small_bold = tkfont.Font(self, family=FONT, size=small, weight="bold")
        self.f_link = tkfont.Font(self, family=FONT, size=small, underline=True)
        self.f_check = tkfont.Font(self, family=ICON_FAMILY if ICON_OK else FONT, size=FS + 6 if ICON_OK else FS + 2)
        self.f_arrow = tkfont.Font(self, family=ICON_FAMILY if ICON_OK else FONT, size=max(10, FS) if ICON_OK
                                   else max(7, FS - 4))
        self.f_handle = tkfont.Font(self, family=ICON_FAMILY if ICON_OK else FONT, size=FS + 4 if ICON_OK else FS)
        self.line_small = self.f_small.metrics("linespace")
        self.line_chip = self.f_chip.metrics("linespace")
        self.cap_w = max(self.f_small.measure("Статус"), self.f_small.measure("Приоритет"))
        self.side_w = max(36, self.f_star.measure(ic("star")), self.f_icon.measure(ic("chat_bubble_outline")) + 12)

    def _btn_leave(self, w):
        on = ((w is getattr(self, "pin_btn", None) and self.cfg["topmost"])
              or (w is getattr(self, "collapse_btn", None) and self.collapsed))
        w.configure(fg=(ACCENT if _lum(BG) < 0.5 else "#0067c0") if on else BG_SUB, bg=HEADER)

    def rebuild(self):
        """Перерисовать всё окно после смены цветов/шрифта (окно настроек не трогаем)."""
        self.unbind_all("<MouseWheel>")
        for w in list(self.winfo_children()):
            if not isinstance(w, tk.Toplevel):
                w.destroy()
        self.configure(bg=BG)
        self._build()
        if self.collapsed:
            self.body.pack_forget()
        if self.last_ok:
            self.status_lbl.configure(text=f"Обновлено в {self.last_ok}")
        self.update_idletasks()
        if self.issues:
            self.render()
        elif not self.cfg["base_url"]:
            self._show_message("Нажмите ⚙, чтобы подключить YouTrack")

    def _placeholder(self, entry, text):
        def show(_=None):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(fg=SUB)
                entry._ph = True

        def hide(_=None):
            if getattr(entry, "_ph", False):
                entry._ph = False
                entry.delete(0, "end")
                entry.configure(fg=TEXT)

        entry.bind("<FocusIn>", hide)
        entry.bind("<FocusOut>", show)
        show()

    def _search_text(self):
        return "" if getattr(self.search, "_ph", False) else self.search_var.get().strip().lower()

    # ----- перемещение/размер
    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.winfo_x(), e.y_root - self.winfo_y()
        self._drag_from = (e.x_root, e.y_root)

    def _drag_move(self, e):
        self.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        x0, y0 = getattr(self, "_drag_from", (e.x_root, e.y_root))
        moved = abs(e.x_root - x0) > 3 or abs(e.y_root - y0) > 3
        if self.collapsed and not moved:  # просто клик по свёрнутой шапке — показать список
            self.toggle_collapse()
        self._save_geometry()

    def _resize_start(self, e):
        self._rs = (e.x_root, e.y_root, self.winfo_width(), self.winfo_height())
        self._rs_target = None
        self._rs_job = None

    def _resize_move(self, e):
        # события мыши приходят чаще, чем нужно: применяем размер не чаще раза за кадр (~60 FPS)
        x0, y0, w0, h0 = self._rs
        self._rs_target = (max(MIN_W, w0 + e.x_root - x0), max(MIN_H, h0 + e.y_root - y0))
        if not self._rs_job:
            self._rs_job = self.after(16, self._resize_apply)

    def _resize_apply(self):
        self._rs_job = None
        if self._rs_target:
            self.geometry("%dx%d" % self._rs_target)

    def _save_geometry(self):
        if self.collapsed:
            w = self.winfo_width()
            h = self._full_height
        else:
            w, h = self.winfo_width(), self.winfo_height()
        self.cfg["geometry"] = f"{w}x{h}+{self.winfo_x()}+{self.winfo_y()}"
        save_cfg(self.cfg)

    def _on_canvas_resize(self, e):
        short = self.winfo_width() < 470  # короткие названия вкладок в узком окне
        if short != getattr(self, "_short_tabs", None):
            self._short_tabs = short
            self._update_tabs()
        # перекладка карточек — один раз на пачку событий, и только если поменялась ширина
        if e.width != self._layout_width and not self._layout_job:
            self._layout_job = self.after_idle(self._relayout)

    def _relayout(self):
        # во время ресайза рисуем только то, что видно на экране, а полную раскладку — когда размер устоялся
        self._layout_job = None
        c = self.canvas
        self._draw_cards(keep_scroll=True, limit_y=c.canvasy(0) + c.winfo_height() + 150)
        if getattr(self, "_full_job", None):
            self.after_cancel(self._full_job)
        self._full_job = self.after(180, self._full_relayout)

    def _full_relayout(self):
        self._full_job = None
        self._draw_cards(keep_scroll=True)

    def _on_wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 60, "units")  # 60 px за щелчок колеса

    # ----- кнопки заголовка
    def toggle_topmost(self):
        self.cfg["topmost"] = not self.cfg["topmost"]
        self.attributes("-topmost", self.cfg["topmost"])
        self._btn_leave(self.pin_btn)
        save_cfg(self.cfg)

    def toggle_collapse(self):
        if not self.collapsed:
            self._full_height = self.winfo_height()
            self.body.pack_forget()
            self.geometry(f"{self.winfo_width()}x40")
        else:
            self.body.pack(fill="both", expand=True)
            self.geometry(f"{self.winfo_width()}x{max(MIN_H, getattr(self, '_full_height', 0))}")
        self.collapsed = not self.collapsed
        self._sync_collapse_btn()

    def _sync_collapse_btn(self):
        """Свёрнут — кнопка становится синей стрелкой «развернуть», чтобы было видно, как вернуть список."""
        b = getattr(self, "collapse_btn", None)
        if b is not None:
            b.configure(text=ic("expand_more") if self.collapsed else ic("remove"))
            self._btn_leave(b)

    def bring_front(self):
        self.deiconify()
        if self.collapsed:
            self.toggle_collapse()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", self.cfg["topmost"]))
        try:  # после горячей клавиши Windows разрешает забрать фокус
            ctypes.windll.user32.SetForegroundWindow(ctypes.windll.user32.GetParent(self.winfo_id()))
        except Exception:
            pass
        self.focus_force()

    def toggle_visible(self):
        """Горячая клавиша: виджет на переднем плане — спрятать, иначе — показать."""
        hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
        if self.state() == "normal" and ctypes.windll.user32.GetForegroundWindow() == hwnd:
            self._save_geometry()
            self.withdraw()
        else:
            self.bring_front()
            self.after(50, lambda: self.search.focus_set())  # сразу можно печатать в поиск

    def setup_hotkey(self):
        stop_hotkey(self._hotkey_tid)
        self._hotkey_tid, ok = None, True
        spec = (self.cfg.get("hotkey") or "").strip()
        if spec:
            self._hotkey_tid, ok = start_hotkey(spec, self.q)
            if not ok:
                self.status_lbl.configure(text=f"Сочетание {spec} занято другой программой — смените в ⚙", fg=ERR)
        return ok

    def setup_tray(self):
        want = bool(self.cfg.get("tray", True))
        if want and not self.tray:
            self.tray = Tray(self.q)
            if not self.tray.start():
                self.tray = None
        elif not want and self.tray:
            self.tray.stop()
            self.tray = None
        self._update_header_count()

    def on_close(self):
        """✕: с включённым треем — спрятать (виджет продолжает следить за задачами), иначе — выйти."""
        if not self.tray:
            self.quit_app()
            return
        self._save_geometry()
        self.withdraw()
        if not self.cfg.get("tray_hint_shown"):
            self.cfg["tray_hint_shown"] = True
            save_cfg(self.cfg)
            show_toast("YouTrack Widget свёрнут в трей",
                       "Виджет продолжает следить за задачами. Открыть — клик по значку в трее"
                       + (f" или {self.cfg['hotkey']}" if self.cfg.get("hotkey") else "") + ".",
                       "", "Выход — правый клик по значку → «Выход»")

    def _on_tray(self, cmd):
        if cmd == 1:      # клик по значку или «Показать / спрятать»
            if self.state() == "withdrawn":
                self.bring_front()
            else:
                self._save_geometry()
                self.withdraw()
        elif cmd == 2:
            self.refresh()
        elif cmd == 3:
            self.mark_all_read()
        elif cmd == 9:
            self.quit_app()

    def quit_app(self):
        self._save_geometry()
        stop_hotkey(self._hotkey_tid)
        if self.tray:
            self.tray.stop()
        self.destroy()

    def destroy(self):
        # отменить отложенные задачи (опрос очереди, автообновление), чтобы не срабатывали после закрытия
        try:
            for job in self.tk.splitlist(self.tk.call("after", "info")):
                self.after_cancel(job)
        except tk.TclError:
            pass
        super().destroy()

    # ----- данные
    def issue_url(self, issue):
        return f"{self.cfg['base_url'].strip().rstrip('/')}/issue/{issue['idReadable']}"

    def open_issue(self, issue):
        if issue:
            os.startfile(self.issue_url(issue))  # браузер по умолчанию
            self.mark_read(issue)

    # ----- новые комментарии
    def new_comments(self, issue) -> int:
        base = self.seen.get(issue.get("idReadable"))
        if base is None:
            return 0
        me = self.me_login.lower()
        return sum(1 for c in live_comments(issue)
                   if (c.get("created") or 0) > base
                   and ((c.get("author") or {}).get("login") or "").lower() != me)

    def mark_read(self, issue, render=True):
        if issue:
            self.seen[issue["idReadable"]] = max(last_comment_time(issue), self.seen.get(issue["idReadable"], 0))
            save_seen(self.seen)
            if render:  # меняем только счётчик на этой карточке и в заголовке
                self._draw_badge(issue["idReadable"], 0)
                self._update_header_count()

    # ----- уведомления о новых задачах и комментариях
    def _notify_events(self, issues):
        me = self.me_login.lower()
        ids = [i.get("idReadable") for i in issues if i.get("idReadable")]
        first = self._notified is None  # первая загрузка после запуска — ни о чём не уведомляем
        if first:
            self._notified = {}
        events = []
        for issue in issues:
            iid = issue.get("idReadable")
            if not iid:
                continue
            last = last_comment_time(issue)
            if first or (self._known_ids is not None and iid not in self._known_ids):
                if not first:
                    events.append(("new", issue, None))
                self._notified[iid] = last
                continue
            base = max(self._notified.get(iid, 0), self.seen.get(iid, 0))
            fresh = [c for c in live_comments(issue) if (c.get("created") or 0) > base
                     and ((c.get("author") or {}).get("login") or "").lower() != me]
            if fresh:
                events.append(("comment", issue, fresh))
            self._notified[iid] = max(self._notified.get(iid, 0), last)
        self._known_ids = set(ids)
        if events and self.cfg.get("notify", True):
            self._show_events(events)

    def _show_events(self, events):
        if len(events) > 3:  # много событий сразу — одно сводное уведомление
            n_new = sum(1 for e in events if e[0] == "new")
            n_com = sum(len(e[2]) for e in events if e[0] == "comment")
            parts = ([f"новых задач: {n_new}"] if n_new else []) + ([f"новых комментариев: {n_com}"] if n_com else [])
            show_toast("YouTrack: " + ", ".join(parts),
                       ", ".join(e[1]["idReadable"] for e in events[:6]) + ("…" if len(events) > 6 else ""),
                       self.cfg["base_url"].rstrip("/") + "/issues")
            return
        for kind, issue, comments in events:
            iid, summary, url = issue["idReadable"], issue.get("summary") or "", self.issue_url(issue)
            if kind == "new":
                prio = field_value(issue, self.cfg["priority_field"], "priority")[0]
                show_toast(f"Новая задача · {iid}", summary, url, f"Приоритет: {prio}" if prio else "")
            else:
                c = comments[-1]
                who = (c.get("author") or {}).get("fullName") or (c.get("author") or {}).get("login") or ""
                more = f" (+{len(comments) - 1})" if len(comments) > 1 else ""
                show_toast(f"Комментарий · {iid}{more}", f"{who}: {comment_snippet(c)}", url, summary)

    # ----- план на день
    def in_plan(self, issue) -> bool:
        return issue.get("idReadable") in self.plan

    def _save_plan(self):
        save_plan(self.plan, self.plan_done)

    def toggle_done(self, issue):
        """Галочка «сделано сегодня» в плане на день (в YouTrack ничего не меняется)."""
        iid = issue.get("idReadable")
        if iid in self.plan_done:
            self.plan_done.discard(iid)
        else:
            self.plan_done.add(iid)
        self._save_plan()
        self.render(keep_scroll=True)

    def move_in_plan(self, ordered_ids):
        """Новый порядок задач плана (после перетаскивания)."""
        rest = [i for i in self.plan if i not in ordered_ids]  # задачи плана, которых сейчас нет в списке
        self.plan = list(ordered_ids) + rest
        self._save_plan()
        self.render(keep_scroll=True)

    def toggle_plan(self, issue):
        iid = issue.get("idReadable")
        if not iid:
            return
        if iid in self.plan:
            self.plan.remove(iid)
            self.plan_done.discard(iid)
        else:
            self.plan.append(iid)
        self._save_plan()
        ref = self.card_items.get(iid)
        if self.cfg.get("tab", "all") == "all" and ref:
            planned = iid in self.plan  # меняем только звёздочку — список не перерисовываем
            self.canvas.itemconfigure(ref["star"], text=ic("star") if planned else ic("star_border"),
                                      fill="#f5b301" if planned else SUB)
            self._update_tabs()
        else:
            self.render(keep_scroll=True)

    def clear_plan(self):
        if self.plan and messagebox.askyesno("План на день", "Убрать все задачи из плана на день?\n"
                                             "Сами задачи в YouTrack не изменятся.", parent=self):
            self.plan = []
            self.plan_done = set()
            self._save_plan()
            self.render()

    def set_tab(self, key):
        if self.cfg.get("tab") != key:
            self.cfg["tab"] = key
            save_cfg(self.cfg)
            self.render()

    def _update_tabs(self):
        if not getattr(self, "tab_widgets", None):
            return
        accent = "#4cc2ff" if _lum(BG) < 0.5 else "#0067c0"
        n_day = sum(1 for i in self.issues if self.in_plan(i)) if self.issues else len(self.plan)
        n_urgent = sum(1 for u in self.urgent if not u["done"])
        short = getattr(self, "_short_tabs", False)  # узкое окно — короткие названия вкладок
        texts = {"all": f"{'Все' if short else 'Все мои задачи'}  {len(self.issues) if self.issues else ''}".rstrip(),
                 "day": f"{'День' if short else 'На день'}  {n_day if n_day else ''}".rstrip(),
                 "urgent": f"Срочные  {n_urgent if n_urgent else ''}".rstrip()}
        cur = self.cfg.get("tab", "all")
        for key, (lbl, bar, icon_lbl) in self.tab_widgets.items():
            active = key == cur
            lbl.configure(text=texts[key], fg=BG_TEXT if active else BG_SUB)
            if icon_lbl is not None:
                on_color = "#f5b301" if key == "day" else "#ef6c00"
                # у «Срочных» значок оранжевый, пока есть невыполненные задачи — даже на другой вкладке
                icon_lbl.configure(fg=on_color if active or (key == "urgent" and n_urgent) else BG_SUB)
            bar.configure(bg=accent if active else BG)
        if cur == "day" and self.plan:
            self.clear_plan_btn.pack(side="right")
        else:
            self.clear_plan_btn.pack_forget()
        # фильтр по приоритету/спринту к своим задачам не относится — на «Срочных» вместо него кнопки
        if getattr(self, "search_box", None) is not None:
            if cur == "urgent":
                self.prio_btn.pack_forget()
                if not self.urgent_btns.winfo_manager():
                    self.urgent_btns.pack(side="right", fill="y", padx=(6, 0), before=self.search_box)
                if any(u["done"] for u in self.urgent):
                    self.sweep_btn.pack(side="right", fill="y", padx=(0, 6))
                else:
                    self.sweep_btn.pack_forget()
            else:
                self.urgent_btns.pack_forget()
                if not self.prio_btn.winfo_manager():
                    self.prio_btn.pack(side="right", fill="y", padx=(6, 0), before=self.search_box)

    def mark_all_read(self):
        for issue in self.issues:
            self.seen[issue["idReadable"]] = max(last_comment_time(issue), self.seen.get(issue["idReadable"], 0))
        save_seen(self.seen)
        self.render(keep_scroll=True)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def refresh(self):
        if self.refresh_job:
            self.after_cancel(self.refresh_job)
            self.refresh_job = None
        token = unprotect(self.cfg["token_enc"])
        if not self.cfg["base_url"] or not token:
            return
        if not self.loading:
            self.loading = True
            self.status_lbl.configure(text="Загрузка…", fg=BG_SUB)
            cfg = dict(self.cfg)

            need_me = not self.me_login

            def work():
                try:
                    if need_me:
                        try:
                            me = fetch_me(cfg["base_url"], token)
                            self.q.put((lambda m: setattr(self, "me_login", m), me))
                        except YTError:
                            pass
                    data = fetch_issues(cfg["base_url"], token, cfg["query"], int(cfg["max_issues"]))
                    self.q.put(("issues", data))
                except YTError as e:
                    self.q.put(("error", str(e)))
                except Exception as e:  # noqa
                    self.q.put(("error", f"Ошибка: {e}"))

            threading.Thread(target=work, daemon=True).start()
        minutes = max(1, int(self.cfg.get("refresh_min") or 5))
        self.refresh_job = self.after(minutes * 60_000, self.refresh)

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "issues":
                    self.loading = False
                    self._notify_events(payload)
                    self.issues = payload
                    self._message = None
                    # задачи, которые видим впервые: уже существующие комментарии считаем прочитанными
                    added = False
                    for issue in payload:
                        if issue.get("idReadable") and issue["idReadable"] not in self.seen:
                            self.seen[issue["idReadable"]] = last_comment_time(issue)
                            added = True
                    if added:
                        save_seen(self.seen)
                    self.last_ok = time.strftime("%H:%M")
                    note = getattr(self, "_status_note", None)
                    fresh = note and time.time() - note[1] < 10  # подтверждение недавнего изменения ещё видно
                    self.status_lbl.configure(text=(f"{note[0]} · " if fresh else "") + f"Обновлено в {self.last_ok}",
                                              fg=BG_SUB)
                    # автообновление раз в N минут: если в YouTrack ничего не поменялось — не перерисовываем
                    sig = hash(json.dumps(payload, sort_keys=True, ensure_ascii=False))
                    if sig != self._data_sig or not self.cards:
                        self._data_sig = sig
                        self.render(keep_scroll=True)
                elif kind == "error":
                    self.loading = False
                    suffix = f" · данные от {self.last_ok}" if self.last_ok else ""
                    self.status_lbl.configure(text=payload + suffix, fg=ERR)
                    if not self.issues:
                        if self.cfg.get("tab") == "urgent":
                            self._message = payload  # свои срочные задачи от YouTrack не зависят — не закрываем их
                        else:
                            self._show_message(payload)
                elif kind == "show":
                    self.bring_front()
                elif kind == "hotkey":
                    self.toggle_visible()
                elif kind == "tray":
                    self._on_tray(payload)
                elif callable(kind):
                    kind(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # ----- отрисовка (все карточки — элементы одного Canvas)
    NO_PRIO = "(без приоритета)"
    NO_SPRINT = "(без спринта)"
    CARD_GAP = 6      # расстояние между карточками
    PAD_L = 14        # отступ текста от левого края карточки (после цветной полоски)
    RIGHT_M = 6       # отступ карточки от полосы прокрутки

    def _clear(self):
        self.canvas.delete("all")
        self.card_items = {}
        self._hover = None
        self._hover_title = None

    def _show_message(self, text):
        """Сообщение вместо списка (нет настроек, ошибка сервера). Запоминается, чтобы пережить перерисовку."""
        self._message = text
        self.cards, self.visible = [], []
        self._clear()
        self._draw_message(text)

    def _draw_message(self, text):
        w = max(200, self.canvas.winfo_width())
        self.canvas.create_text(w // 2, 40, text=text, fill=BG_SUB, font=self.f_msg, width=w - 40,
                                justify="center", anchor="n")
        self.canvas.configure(scrollregion=(0, 0, w, 120))
        self.canvas.yview_moveto(0)

    # ----- фильтры (поиск + приоритет): данные не перезапрашиваются, карточки просто перерисовываются
    def _schedule_filter(self):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(350, self._apply_filter_now)

    def _apply_filter_now(self):
        if self._search_job:
            self.after_cancel(self._search_job)
            self._search_job = None
        self._apply_filter(keep_scroll=False)

    def _prio_filter(self) -> set:
        return set(self.cfg.get("prio_filter") or [])

    def _update_prio_button(self):
        sel = [p for p in (self.cfg.get("prio_filter") or [])]
        hot = bool(self.cfg.get("due_hot"))
        parts = []
        if len(sel) == 1:
            parts.append(sel[0])
        elif sel:
            parts.append(f"Приоритет: {len(sel)}")
        if hot:
            parts.append("Горящие")
        spr = self.cfg.get("sprint_filter") or []
        if len(spr) == 1:
            parts.append(spr[0] if spr[0] != self.NO_SPRINT else "Без спринта")
        elif spr:
            parts.append(f"Спринтов: {len(spr)}")
        text = " · ".join(parts) if parts else "Фильтр: все"
        sel = sel or hot or spr
        fg = ACCENT if sel else TEXT
        self.prio_text.configure(text=text, fg=fg)
        self.prio_icon.configure(fg=ACCENT if sel else SUB)
        self.prio_arrow.configure(fg=fg)

    def _prio_hover(self, on):
        bg = CARD_HOVER if on else CARD
        for w in (self.prio_btn, self.prio_icon, self.prio_text, self.prio_arrow):
            w.configure(bg=bg)

    def _rebuild_prio_menu(self):
        """Пункты меню — приоритеты, которые реально есть в задачах (в порядке сортировки YouTrack)."""
        pf = self.cfg["priority_field"]
        values = []
        for issue in self.issues:
            p = field_value(issue, pf, "priority")[0] or self.NO_PRIO
            if p not in values:
                values.append(p)
        if self.NO_PRIO in values:  # «без приоритета» — в конец списка
            values.remove(self.NO_PRIO)
            values.append(self.NO_PRIO)
        sel = self._prio_filter()
        values += [p for p in sel if p not in values]
        self.prio_menu.delete(0, "end")
        self.prio_vars = {}
        self.due_hot_var = tk.BooleanVar(value=bool(self.cfg.get("due_hot")))
        self.prio_menu.add_checkbutton(label="Горящие сроки (просрочено, сегодня, завтра)",
                                       variable=self.due_hot_var, command=self._on_prio_change)
        self.prio_menu.add_separator()
        self.prio_menu.add_command(label="Приоритет:", state="disabled")
        for p in values:
            v = tk.BooleanVar(value=p in sel)
            self.prio_vars[p] = v
            self.prio_menu.add_checkbutton(label=p, variable=v, command=self._on_prio_change)
        # спринты, которые есть в задачах: текущие сверху
        sp_sel = set(self.cfg.get("sprint_filter") or [])
        sp_all, has_none = {}, False
        for issue in self.issues:
            lst = sprints_of(issue, self.cfg.get("sprint_field", ""))
            has_none |= not lst
            for n, active in lst:
                sp_all[n] = sp_all.get(n, False) or active
        names = sorted(sp_all, key=lambda n: (not sp_all[n], n))
        if has_none:
            names.append(self.NO_SPRINT)
        names += [n for n in sp_sel if n not in names]
        self.sprint_vars = {}
        if names and (sp_all or sp_sel):
            self.prio_menu.add_separator()
            self.prio_menu.add_command(label="Спринт:", state="disabled")
            for n in names:
                v = tk.BooleanVar(value=n in sp_sel)
                self.sprint_vars[n] = v
                label = n + ("  · текущий" if sp_all.get(n) else "")
                self.prio_menu.add_checkbutton(label=label, variable=v, command=self._on_prio_change)
        self.prio_menu.add_separator()
        self.prio_menu.add_command(label="Сбросить фильтры", command=self._reset_prio)

    def _on_prio_change(self):
        self.cfg["prio_filter"] = [p for p, v in self.prio_vars.items() if v.get()]
        self.cfg["due_hot"] = bool(self.due_hot_var.get())
        self.cfg["sprint_filter"] = [n for n, v in getattr(self, "sprint_vars", {}).items() if v.get()]
        save_cfg(self.cfg)
        self._update_prio_button()
        self._apply_filter(keep_scroll=False)

    def _reset_prio(self):
        for v in list(self.prio_vars.values()) + list(getattr(self, "sprint_vars", {}).values()):
            v.set(False)
        self.due_hot_var.set(False)
        self._on_prio_change()

    def _apply_filter(self, keep_scroll=True):
        flt = self._search_text()
        if self.cfg.get("tab") == "urgent":  # на «Срочных» — только поиск
            self.visible = [c for c in self.cards if not flt or flt in c["hay"]]
            self._draw_cards(keep_scroll=keep_scroll)
            return
        prios = self._prio_filter()
        hot = bool(self.cfg.get("due_hot"))
        sprints = set(self.cfg.get("sprint_filter") or [])
        self.visible = [c for c in self.cards
                        if (not flt or flt in c["hay"]) and (not prios or (c["prio"] or self.NO_PRIO) in prios)
                        and (not hot or (c["due"] and c["due"][3]))
                        and (not sprints or ({n for n, _ in c["sprints"]} or {self.NO_SPRINT}) & sprints)]
        self._draw_cards(keep_scroll=keep_scroll)

    def render(self, keep_scroll=False):
        """Подготовить данные карточек (после загрузки, смены вкладки/плана/темы) и нарисовать."""
        sf, pf = self.cfg["state_field"], self.cfg["priority_field"]
        tab = self.cfg.get("tab", "all")
        if self._done_date != _today():  # наступил новый день — галочки «сделано» сбрасываются
            self._done_date, self.plan_done = _today(), set()
            self._save_plan()
        self.cards = []
        issues = self.issues
        if tab == "urgent":  # свои срочные задачи: невыполненные — новые сверху, выполненные — в конец
            issues = []
            for u in sorted(self.urgent, key=lambda u: (u["done"], -(u["done_at"] if u["done"] else u["created"]))):
                self.cards.append({"kind": "local", "id": u["id"], "summary": u["title"], "text": u["text"],
                                   "created": u["created"], "done": u["done"], "item": u,
                                   "hay": f"{u['title']} {u['text']}".lower()})
        if tab == "day":  # порядок — как расставил пользователь, выполненные — в конец
            order = {iid: n for n, iid in enumerate(self.plan)}
            issues = sorted((i for i in self.issues if self.in_plan(i)),
                            key=lambda i: (i.get("idReadable") in self.plan_done, order.get(i.get("idReadable"), 0)))
        for issue in issues:
            if tab == "day" and not self.in_plan(issue):
                continue
            state, state_color = field_value(issue, sf, "state")
            prio, prio_color = field_value(issue, pf, "priority")
            summary = issue.get("summary") or "(без названия)"
            due = due_info(due_date(issue, self.cfg.get("due_field", "")))
            self.cards.append({
                "issue": issue, "id": issue.get("idReadable", ""), "summary": summary,
                "state": state, "state_colors": chip_colors(state, state_color) if state else None,
                "prio": prio, "prio_colors": chip_colors(prio, prio_color) if prio else None,
                "due": due,
                "done": tab == "day" and issue.get("idReadable") in self.plan_done,
                "sprints": sprints_of(issue, self.cfg.get("sprint_field", "")),
                "has_sprint": sprint_field_of(issue, self.cfg.get("sprint_field", "")) is not None,
                "bar": chip_colors(prio, prio_color)[0] if prio else "#3a3a3a",
                "hay": f"{issue.get('idReadable', '')} {summary} {state or ''} {prio or ''} "
                       f"{due[0] if due else ''} "
                       f"{' '.join(n for n, _ in sprints_of(issue, self.cfg.get('sprint_field', '')))}".lower(),
            })
        self._update_header_count()
        self._update_tabs()
        self._rebuild_prio_menu()
        self._update_prio_button()
        self._apply_filter(keep_scroll=keep_scroll)

    def _update_header_count(self):
        total_new = sum(self.new_comments(i) for i in self.issues)
        if self.tray:  # число на значке в трее и подсказка при наведении
            tip = f"YouTrack: задач {len(self.issues)}" if self.issues else "YouTrack Widget"
            self.tray.update(total_new, tip + (f" · новых комментариев: {total_new}" if total_new else ""))
        self.count_lbl.configure(text=f"{total_new} нов." if total_new else "")
        if total_new:  # значок chat_bubble перед числом новых комментариев
            self.count_icon.pack(side="left", padx=(2, 3), before=self.count_lbl)
        else:
            self.count_icon.pack_forget()

    def _empty_text(self):
        if self.cfg.get("tab") == "urgent":
            if self._search_text():
                return "Ничего не найдено"
            return ("Срочных задач нет.\n\nНажмите «+ Новая», чтобы записать короткое дело — "
                    "например, «Написать Ивану, уточнить сроки».\nЗадачи хранятся только в виджете.")
        if (self._search_text() or self._prio_filter() or self.cfg.get("due_hot")
                or self.cfg.get("sprint_filter")):
            return "Ничего не найдено"
        if self.cfg.get("tab", "all") == "day" and self.issues:
            return ("План на день пуст.\n\nНажмите звёздочку на карточке во вкладке "
                    "«Все мои задачи», чтобы добавить задачу в план.")
        if self.issues or self.last_ok:
            return "Задач нет 🎉"
        if getattr(self, "_message", None):
            return self._message
        if not self.cfg["base_url"] or not unprotect(self.cfg["token_enc"]):
            return "Нажмите ⚙, чтобы подключить YouTrack"
        return ""

    def _draw_cards(self, keep_scroll=True, limit_y=None):
        """Нарисовать карточки под текущую ширину холста.
        limit_y — рисовать только до этой высоты (быстрый режим во время ресайза)."""
        c = self.canvas
        width = c.winfo_width()
        if width <= 1:  # окно ещё не показано — нарисуем при первом <Configure>
            self._layout_width = 0
            return
        top = c.canvasy(0) if keep_scroll else 0  # сохраняем позицию прокрутки в пикселях
        self._layout_width = width
        self._clear()
        if not self.visible:
            text = self._empty_text()
            if text:
                self._draw_message(text)
            else:
                c.configure(scrollregion=(0, 0, width, 1))
            return
        x0, x1 = 0, width - self.RIGHT_M
        side_x = x1 - self.side_w - 4               # левая граница колонки значков
        tx = x0 + 4 + self.PAD_L                     # начало текста
        self._day_mode = self.cfg.get("tab", "all") == "day"
        if self._day_mode:                           # в плане на день слева ручка и галочка
            tx += 48
        elif self.cfg.get("tab") == "urgent":        # у срочных задач слева только галочка
            tx += 30
        self._card_bounds = []                       # (верх, низ) каждой карточки — для перетаскивания
        wrap = max(80, side_x - tx - 6)              # ширина переноса названия
        y = 0
        heights = self.__dict__.setdefault("_card_heights", {})
        for i, card in enumerate(self.visible):
            if limit_y is not None and y > limit_y:
                # остальные карточки пока не рисуем — берём их высоту из прошлой раскладки
                y += sum(heights.get(cd["id"], 110) + self.CARD_GAP for cd in self.visible[i:])
                break
            bottom = self._draw_card(i, card, x0, x1, y, tx, wrap, side_x)
            heights[card["id"]] = bottom - y
            self._card_bounds.append((y, bottom))
            y = bottom + self.CARD_GAP
        visible_h = c.winfo_height()
        total = max(y, visible_h)  # короткий список не «плавает»: область прокрутки не меньше окна
        c.configure(scrollregion=(0, 0, width, total))
        c.yview_moveto(min(max(0, top), max(0, total - visible_h)) / total if total else 0)

    def _draw_local_card(self, i, card, x0, x1, y, tx, wrap, side_x):
        """Карточка своей срочной задачи: галочка, время создания, название, текст; справа — изменить/удалить."""
        c = self.canvas
        t = f"c{i}"
        done = card["done"]
        bg = c.create_rectangle(x0, y, x1, y + 10, fill=CARD, outline="", tags=(t, "bg"))
        bar = c.create_rectangle(x0, y, x0 + 4, y + 10, fill="#757575" if done else "#ef6c00", outline="", tags=(t,))
        c.create_text(x0 + 22, y + 24, text=ic("check_circle") if done else ic("radio_button_unchecked"),
                      fill="#43a047" if done else SUB, font=self.f_check, tags=(t, f"k{i}"))
        cy = y + 8
        stamp = card["item"].get("done_at") if done else card["created"]
        when = time.strftime("%d.%m %H:%M", time.localtime(stamp / 1000)) if stamp else ""
        c.create_text(tx, cy, text=("выполнено · " if done else "") + when, fill=SUB, font=self.f_small,
                      anchor="nw", tags=(t,))
        cy += self.line_small + 1
        title = c.create_text(tx, cy, text=card["summary"], fill=SUB if done else TEXT,
                              font=self.f_title_done if done else self.f_title, anchor="nw", width=wrap,
                              tags=(t, f"t{i}"))
        cy = c.bbox(title)[3] + 4
        if card["text"]:
            body = c.create_text(tx, cy, text=card["text"], fill=SUB, font=self.f_body, anchor="nw",
                                 width=wrap, tags=(t,))
            cy = c.bbox(body)[3] + 4
        ix = side_x + self.side_w // 2
        c.create_text(ix, y + 22, text=ic("edit"), fill=SUB, font=self.f_icon, tags=(t, f"e{i}"))
        trash = c.create_text(ix, y + 56, text=ic("delete_outline"), fill=SUB, font=self.f_icon, tags=(t, f"x{i}"))
        bottom = max(cy + 6, c.bbox(trash)[3] + 6)
        c.coords(bg, x0, y, x1, bottom)
        c.coords(bar, x0, y, x0 + 4, bottom)
        return bottom

    def _draw_card(self, i, card, x0, x1, y, tx, wrap, side_x):
        if card.get("kind") == "local":
            return self._draw_local_card(i, card, x0, x1, y, tx, wrap, side_x)
        c = self.canvas
        t = f"c{i}"
        issue = card["issue"]
        bg = c.create_rectangle(x0, y, x1, y + 10, fill=CARD, outline="", tags=(t, "bg"))
        bar = c.create_rectangle(x0, y, x0 + 4, y + 10, fill=card["bar"], outline="", tags=(t,))
        cy = y + 8
        if self._day_mode:  # ручка для перетаскивания и галочка «сделано сегодня»
            c.create_text(x0 + 16, y + 24, text=ic("drag_indicator"), fill=SUB, font=self.f_handle,
                          tags=(t, f"h{i}"))
            done = card["done"]
            c.create_text(x0 + 42, y + 24, text=ic("check_circle") if done else ic("radio_button_unchecked"),
                          fill="#43a047" if done else SUB, font=self.f_check, tags=(t, f"k{i}"))
        c.create_text(tx, cy, text=card["id"], fill=SUB, font=self.f_small, anchor="nw", tags=(t,))
        cy += self.line_small + 1
        title = c.create_text(tx, cy, text=card["summary"], fill=SUB if card["done"] else TEXT,
                              font=self.f_title_done if card["done"] else self.f_title, anchor="nw",
                              width=wrap, tags=(t, f"t{i}"))
        cy = c.bbox(title)[3] + 6
        # (подпись, значение, цвета, роль): роль a/p/r — плашка кликабельна и открывает список значений из YouTrack
        rows = [("Статус", card["state"], card["state_colors"], "a"),
                ("Приоритет", card["prio"], card["prio_colors"], "p")]
        if card["due"]:  # строка «Срок» — только если срок задан
            dtext, dbg, dfg, _ = card["due"]
            rows.append(("Срок", dtext, (dbg, dfg) if dbg else chip_colors(dtext, None), None))
        if card["sprints"]:  # строка «Спринт»: текущий спринт синим, прошедшие — серым
            name, active = card["sprints"][0]
            more = f" +{len(card['sprints']) - 1}" if len(card["sprints"]) > 1 else ""
            rows.append(("Спринт", name + more, ("#1565c0", "#ffffff") if active else chip_colors(name, None), "r"))
        elif card.get("has_sprint"):  # спринта нет, но поле есть — можно назначить
            rows.append(("Спринт", None, None, "r"))
        arrow = ic("arrow_drop_down") if ICON_OK else "▾"
        for caption, value, colors, role in rows:
            c.create_text(tx, cy + 1, text=caption, fill=SUB, font=self.f_small, anchor="nw", tags=(t,))
            vx = tx + self.cap_w + 10
            tags = (t, f"{role}{i}") if role else (t,)
            if value:
                cbg, cfg = colors
                tw = self.f_chip.measure(value)
                aw = self.f_arrow.measure(arrow) if role else 0  # стрелка ▾ — подсказка, что это выпадающий список
                c.create_rectangle(vx, cy, vx + tw + 10 + aw, cy + self.line_chip + 2, fill=cbg, outline="",
                                   tags=tags)
                c.create_text(vx + 5, cy + 1, text=value, fill=cfg, font=self.f_chip, anchor="nw", tags=tags)
                if role:
                    c.create_text(vx + 5 + tw + aw / 2 + 1, cy + (self.line_chip + 2) / 2, text=arrow, fill=cfg,
                                  font=self.f_arrow, tags=tags)
            elif role:  # пусто, но можно выбрать: «— ▾» в рамке
                tw = self.f_small.measure("выбрать")
                aw = self.f_arrow.measure(arrow)
                c.create_rectangle(vx, cy, vx + tw + 10 + aw, cy + self.line_chip + 2, fill="",
                                   outline=SUB, dash=(2, 2), tags=tags)
                c.create_text(vx + 5, cy + 1, text="выбрать", fill=SUB, font=self.f_small, anchor="nw", tags=tags)
                c.create_text(vx + 5 + tw + aw / 2 + 1, cy + (self.line_chip + 2) / 2, text=arrow, fill=SUB,
                              font=self.f_arrow, tags=tags)
            else:
                c.create_text(vx, cy + 1, text="—", fill=SUB, font=self.f_small, anchor="nw", tags=(t,))
            cy += self.line_chip + 5
        # колонка значков справа: комментарии и звёздочка «план на день»
        ix = side_x + self.side_w // 2
        opened = card["id"] in self.expanded
        icon = c.create_text(ix - 3, y + 22, text=ic("chat_bubble" if opened else "chat_bubble_outline")
                             if ICON_OK else "💬", fill=ACCENT if opened else SUB, font=self.f_icon, tags=(t, f"i{i}"))
        planned = self.in_plan(issue)
        star = c.create_text(ix, y + 56, text=ic("star") if planned else ic("star_border"),
                             fill="#f5b301" if planned else SUB, font=self.f_star, tags=(t, f"s{i}"))
        bottom = max(cy + 3, c.bbox(star)[3] + 6)
        if opened:  # быстрый просмотр: описание и последние комментарии прямо в карточке
            bottom = self._draw_details(i, card, x0 + 4 + self.PAD_L, x1 - 12, bottom + 2, t)
        c.coords(bg, x0, y, x1, bottom)
        c.coords(bar, x0, y, x0 + 4, bottom)
        self.card_items[card["id"]] = {"i": i, "icon": icon, "star": star, "badge": [], "x": ix + 8, "y": y + 9}
        self._draw_badge(card["id"], 0 if opened else self.new_comments(issue))
        return bottom

    def _draw_details(self, i, card, lx, rx, y, t):
        """Раскрытая часть карточки: описание + 3 последних комментария + ссылка «Открыть в YouTrack»."""
        c = self.canvas
        width = max(80, rx - lx)
        c.create_line(lx, y, rx, y, fill=CARD_HOVER if _lum(CARD) < 0.5 else "#d9dce1", tags=(t,))
        y += 8
        c.create_text(lx, y, text="Описание", fill=SUB, font=self.f_small_bold, anchor="nw", tags=(t,))
        y += self.line_small + 3
        desc = self.details.get(card["id"])
        if card["id"] not in self.details or desc is None:
            text, color = "Загрузка…", SUB
        elif isinstance(desc, Exception):
            text, color = f"Не удалось загрузить: {desc}", ERR
        else:
            text = clean_markdown(desc, 700) or "Нет описания"
            color = TEXT if desc.strip() else SUB
        item = c.create_text(lx, y, text=text, fill=color, font=self.f_body, anchor="nw", width=width, tags=(t,))
        y = c.bbox(item)[3] + 10
        comments = live_comments(card["issue"])[-3:][::-1]  # три последних, новые сверху
        c.create_text(lx, y, text="Последние комментарии" if comments else "Комментариев нет",
                      fill=SUB, font=self.f_small_bold, anchor="nw", tags=(t,))
        y += self.line_small + 4
        for cm in comments:
            who = (cm.get("author") or {}).get("fullName") or (cm.get("author") or {}).get("login") or ""
            when = time.strftime("%d.%m %H:%M", time.localtime((cm.get("created") or 0) / 1000))
            head = c.create_text(lx, y, text=who, fill=TEXT, font=self.f_small_bold, anchor="nw", tags=(t,))
            c.create_text(c.bbox(head)[2] + 6, y, text=when, fill=SUB, font=self.f_small, anchor="nw", tags=(t,))
            y += self.line_small + 2
            body = c.create_text(lx, y, text=clean_markdown(cm.get("text") or "", 400) or "—", fill=TEXT,
                                 font=self.f_body, anchor="nw", width=width, tags=(t,))
            y = c.bbox(body)[3] + 8
        link = c.create_text(lx, y + 2, text="Открыть в YouTrack", fill=ACCENT, font=self.f_link, anchor="nw",
                             tags=(t, f"o{i}"))
        c.create_text(c.bbox(link)[2] + 4, y + 1, text=ic("open_in_new"), fill=ACCENT,
                      font=(ICON_FAMILY, max(9, FS - 1)) if ICON_OK else self.f_small, anchor="nw", tags=(t, f"o{i}"))
        return c.bbox(link)[3] + 10

    def _draw_badge(self, iid, count):
        """Красный кружок с числом новых комментариев на значке chat_bubble_outline."""
        ref = self.card_items.get(iid)
        if not ref:
            return
        c = self.canvas
        for item in ref["badge"]:
            c.delete(item)
        ref["badge"] = []
        c.itemconfigure(ref["icon"], fill=TEXT if count else SUB)
        if count:
            label = str(count) if count < 100 else "99+"
            r = 7 if len(label) == 1 else 9
            cx, cy = ref["x"], ref["y"]
            tag = f"c{ref['i']}"
            bg_now = c.itemcget(c.find_withtag(f"c{ref['i']}&&bg")[0], "fill") if c.find_withtag(f"c{ref['i']}&&bg") else CARD
            ref["badge"] = [
                c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=BADGE, outline=bg_now, width=1.5,
                              tags=(tag, f"i{ref['i']}", "badge_ring")),
                c.create_text(cx, cy, text=label, fill="white", font=self.f_badge, tags=(tag, f"i{ref['i']}")),
            ]

    # ----- мышь над списком
    def _hit(self, e):
        """Что под курсором: (индекс карточки, роль) — роль 't' название, 'i' комментарии, 's' звёздочка."""
        c = self.canvas
        x, y = c.canvasx(e.x), c.canvasy(e.y)
        card, role = None, None
        for item in reversed(c.find_overlapping(x, y, x, y)):  # сверху вниз
            for tag in c.gettags(item):
                head, num = tag[:1], tag[1:]
                if num.isdigit():
                    if head == "c" and card is None:
                        card = int(num)
                    elif head in "tisokhexapr" and role is None:
                        role = head
            if card is not None:
                break
        if card is None or card >= len(self.visible):
            return None, None
        return card, role

    def _on_motion(self, e):
        card, role = self._hit(e)
        self._set_hover(card, card if role == "t" else None)
        self.canvas.configure(cursor="fleur" if role == "h" else "hand2" if role else "")

    def _on_canvas_leave(self, e):
        self.unbind_all("<MouseWheel>")
        self._set_hover(None, None)
        self.canvas.configure(cursor="")

    def _set_hover(self, card, title):
        c = self.canvas
        if card != self._hover:
            for idx, color in ((self._hover, CARD), (card, CARD_HOVER)):
                if idx is None:
                    continue
                for item in c.find_withtag(f"c{idx}&&bg"):
                    c.itemconfigure(item, fill=color)
                for ring in c.find_withtag(f"c{idx}&&badge_ring"):
                    c.itemconfigure(ring, outline=color)
            self._hover = card
        if title != getattr(self, "_hover_title", None):
            for idx, on in ((getattr(self, "_hover_title", None), False), (title, True)):
                if idx is not None and idx < len(self.visible):
                    done = self.visible[idx]["done"]
                    for item in c.find_withtag(f"t{idx}"):
                        c.itemconfigure(item, fill=ACCENT if on else (SUB if done else TEXT),
                                        font=self.f_title_ul if on else (self.f_title_done if done else self.f_title))
            self._hover_title = title

    # клик выполняется при отпускании кнопки: так нажатие можно превратить в перетаскивание
    def _on_press(self, e):
        card, role = self._hit(e)
        self._press = (card, role, e.x, self.canvas.canvasy(e.y))
        self._drag = None

    def _can_drag(self):
        return (self.cfg.get("tab", "all") == "day" and not self._search_text()
                and not self._prio_filter() and not self.cfg.get("due_hot") and not self.cfg.get("sprint_filter"))

    def _on_drag(self, e):
        press = getattr(self, "_press", None)
        if not press or press[0] is None or press[1] not in (None, "h"):
            return
        c = self.canvas
        y = c.canvasy(e.y)
        if self._drag is None:
            if abs(y - press[3]) < 6:
                return
            if not self._can_drag():
                if self.cfg.get("tab", "all") == "day":
                    self.status_lbl.configure(text="Сбросьте поиск и фильтры, чтобы менять порядок", fg=BG_SUB)
                return
            self._drag = {"card": press[0], "last_y": press[3], "line": None}
            c.tag_raise(f"c{press[0]}")
            c.configure(cursor="fleur")
        d = self._drag
        c.move(f"c{d['card']}", 0, y - d["last_y"])  # карточка едет за мышью
        d["last_y"] = y
        # куда вставим: по середине карточек
        target = len(self._card_bounds)
        for n, (top, bottom) in enumerate(self._card_bounds):
            if y < (top + bottom) / 2:
                target = n
                break
        d["target"] = target
        line_y = (self._card_bounds[target][0] - self.CARD_GAP / 2 if target < len(self._card_bounds)
                  else self._card_bounds[-1][1] + self.CARD_GAP / 2)
        if d["line"]:
            c.coords(d["line"], 0, line_y, self._layout_width - self.RIGHT_M, line_y)
        else:
            d["line"] = c.create_line(0, line_y, self._layout_width - self.RIGHT_M, line_y,
                                      fill=ACCENT, width=3)
        # автопрокрутка у краёв списка (только если список длиннее окна)
        if self._card_bounds and self._card_bounds[-1][1] > c.winfo_height():
            if e.y < 24:
                c.yview_scroll(-12, "units")
            elif e.y > c.winfo_height() - 24:
                c.yview_scroll(12, "units")

    def _on_release(self, e):
        press, self._press = getattr(self, "_press", None), None
        if self._drag:
            d, self._drag = self._drag, None
            self.canvas.configure(cursor="")
            ids = [cd["id"] for cd in self.visible]
            src, target = d["card"], d.get("target", d["card"])
            moved = ids.pop(src)
            ids.insert(target - 1 if target > src else target, moved)
            self.move_in_plan(ids)
            return
        if not press or press[0] is None:
            return
        card, role = self._hit(e)
        if card != press[0] or role != press[1]:
            return  # отпустили не там, где нажали
        if self.visible[card].get("kind") == "local":  # своя срочная задача
            item = self.visible[card]["item"]
            if role in ("t", "e"):
                self.open_urgent_editor(item)
            elif role == "k":
                self.toggle_urgent_done(item)
            elif role == "x":
                self.delete_urgent(item)
            return
        issue = self.visible[card]["issue"]
        if role in ("t", "o"):
            self.open_issue(issue)
        elif role == "i":
            self.toggle_details(issue)
        elif role == "s":
            self.toggle_plan(issue)
        elif role == "k":
            self.toggle_done(issue)
        elif role in self.FIELD_MENUS:  # клик по плашке статуса / приоритета / спринта
            self.open_field_menu(card, role)

    # ----- быстрый просмотр
    def toggle_details(self, issue):
        iid = issue.get("idReadable")
        if iid in self.expanded:
            self.expanded.discard(iid)
        else:
            self.expanded.add(iid)
            self.mark_read(issue, render=False)  # комментарии посмотрели прямо в виджете
            self._update_header_count()
            if iid not in self.details or isinstance(self.details.get(iid), Exception):
                self.details[iid] = None  # загружается
                token, base = unprotect(self.cfg["token_enc"]), self.cfg["base_url"]

                def work():
                    try:
                        res = fetch_description(base, token, issue)
                    except Exception as ex:  # noqa
                        res = YTError(str(ex))
                    self.q.put((self._details_loaded, (iid, res)))

                threading.Thread(target=work, daemon=True).start()
        self._draw_cards(keep_scroll=True)

    def _details_loaded(self, res):
        iid, data = res
        self.details[iid] = data
        if iid in self.expanded:
            self._draw_cards(keep_scroll=True)

    def _on_rclick(self, e):
        card, _ = self._hit(e)
        if card is None:
            return
        if self.visible[card].get("kind") == "local":
            item = self.visible[card]["item"]
            m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER, activeforeground=TEXT, bd=0)
            m.add_command(label="Изменить", command=lambda: self.open_urgent_editor(item))
            m.add_command(label="Вернуть в работу" if item["done"] else "Отметить выполненной",
                          command=lambda: self.toggle_urgent_done(item))
            m.add_command(label="Копировать текст",
                          command=lambda: self._copy(item["title"] + ("\n" + item["text"] if item["text"] else "")))
            m.add_separator()
            m.add_command(label="Удалить", command=lambda: self.delete_urgent(item))
            m.tk_popup(e.x_root, e.y_root)
            return
        self._card_menu(e, self.visible[card]["issue"])

    # ----- свои срочные задачи (хранятся только в виджете)
    def _save_urgent(self):
        save_urgent(self.urgent)
        self.render(keep_scroll=True)

    def add_urgent(self, title, text):
        title, text = title.strip()[:URGENT_TITLE_MAX], text.strip()[:URGENT_TEXT_MAX]
        if not title:
            return None
        import uuid
        item = {"id": "U-" + uuid.uuid4().hex[:8], "title": title, "text": text,
                "created": int(time.time() * 1000), "done": False, "done_at": 0}
        self.urgent.append(item)
        self._save_urgent()
        return item

    def update_urgent(self, item, title, text):
        title = title.strip()[:URGENT_TITLE_MAX]
        if not title:
            return
        item["title"], item["text"] = title, text.strip()[:URGENT_TEXT_MAX]
        self._save_urgent()

    def toggle_urgent_done(self, item):
        item["done"] = not item["done"]
        item["done_at"] = int(time.time() * 1000) if item["done"] else 0
        self._save_urgent()

    def delete_urgent(self, item):
        if messagebox.askyesno("Срочные задачи", f"Удалить задачу «{item['title']}»?", parent=self):
            self.urgent = [u for u in self.urgent if u["id"] != item["id"]]
            self._save_urgent()

    def clear_done_urgent(self):
        done = [u for u in self.urgent if u["done"]]
        if done and messagebox.askyesno("Срочные задачи", f"Удалить выполненные задачи ({len(done)})?", parent=self):
            self.urgent = [u for u in self.urgent if not u["done"]]
            self._save_urgent()

    def open_urgent_editor(self, item=None):
        """Окно создания/изменения срочной задачи: название до 100 символов, текст до 1000."""
        if getattr(self, "_urgent_win", None) and self._urgent_win.winfo_exists():
            self._urgent_win.destroy()
        BG_, CARD_, TEXT_, SUB_ = DLG_BG, DLG_CARD, DLG_TEXT, DLG_SUB
        win = tk.Toplevel(self)
        self._urgent_win = win
        win.title("Изменить срочную задачу" if item else "Новая срочная задача")
        win.configure(bg=BG_, padx=16, pady=12)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

        def head(row, text, counter_var):
            fr = tk.Frame(win, bg=BG_)
            fr.grid(row=row, column=0, sticky="we", pady=(8, 2))
            tk.Label(fr, text=text, bg=BG_, fg=TEXT_, font=(FONT, 9)).pack(side="left")
            cnt = tk.Label(fr, textvariable=counter_var, bg=BG_, fg=SUB_, font=(FONT, 8))
            cnt.pack(side="right")
            return cnt

        title_cnt, text_cnt = tk.StringVar(), tk.StringVar()
        c1 = head(0, "Название", title_cnt)
        v_title = tk.StringVar(value=item["title"] if item else "")
        # ограничение 100 символов: лишнее просто не вводится (и при вставке обрезается)
        e_title = tk.Entry(win, textvariable=v_title, bg=CARD_, fg=TEXT_, insertbackground=TEXT_, relief="flat",
                           font=(FONT, 11), width=44)
        e_title.grid(row=1, column=0, sticky="we", ipady=5, ipadx=6)

        def on_title(*_):
            s = v_title.get()
            if len(s) > URGENT_TITLE_MAX:
                v_title.set(s[:URGENT_TITLE_MAX])
                self.bell()
                return
            title_cnt.set(f"{len(s)}/{URGENT_TITLE_MAX}")
            c1.configure(fg="#ef6c00" if len(s) >= URGENT_TITLE_MAX - 10 else SUB_)
            err.configure(text="")

        v_title.trace_add("write", on_title)
        c2 = head(2, "Текст (необязательно)", text_cnt)
        t_text = tk.Text(win, bg=CARD_, fg=TEXT_, insertbackground=TEXT_, relief="flat", font=(FONT, 10),
                         width=44, height=7, wrap="word", padx=6, pady=5, undo=True)
        t_text.grid(row=3, column=0, sticky="we")
        if item and item["text"]:
            t_text.insert("1.0", item["text"])

        def on_text(_=None):
            s = t_text.get("1.0", "end-1c")
            if len(s) > URGENT_TEXT_MAX:
                t_text.delete(f"1.0+{URGENT_TEXT_MAX}c", "end")
                self.bell()
                s = s[:URGENT_TEXT_MAX]
            text_cnt.set(f"{len(s)}/{URGENT_TEXT_MAX}")
            c2.configure(fg="#ef6c00" if len(s) >= URGENT_TEXT_MAX - 50 else SUB_)
            t_text.edit_modified(False)

        t_text.bind("<<Modified>>", on_text)
        err = tk.Label(win, text="", bg=BG_, fg=ERR, font=(FONT, 9), anchor="w")
        err.grid(row=4, column=0, sticky="we", pady=(6, 0))
        tk.Label(win, text="Ctrl+Enter — сохранить, Esc — отмена. Задача хранится только в виджете, "
                           "в YouTrack не попадает.", bg=BG_, fg=SUB_, font=(FONT, 8), justify="left",
                 wraplength=380).grid(row=5, column=0, sticky="w")

        def save(_=None):
            title = v_title.get().strip()
            text = t_text.get("1.0", "end-1c")
            if not title:
                err.configure(text="Введите название задачи")
                e_title.focus_set()
                return "break"
            if item:
                self.update_urgent(item, title, text)
            else:
                self.add_urgent(title, text)
                if self.cfg.get("tab") != "urgent":
                    self.set_tab("urgent")
            win.destroy()
            return "break"

        btns = tk.Frame(win, bg=BG_)
        btns.grid(row=6, column=0, sticky="e", pady=(10, 0))
        bstyle = dict(relief="flat", font=(FONT, 9), padx=12, pady=4, cursor="hand2", bd=0)
        tk.Button(btns, text="Отмена", command=win.destroy, bg=CARD_, fg=TEXT_, activebackground=DLG_HOVER,
                  activeforeground=TEXT_, **bstyle).pack(side="left", padx=4)
        tk.Button(btns, text="Сохранить" if item else "Создать", command=save, bg="#0067c0", fg="white",
                  activebackground="#1975c5", activeforeground="white", **bstyle).pack(side="left", padx=4)
        e_title.bind("<Return>", lambda e: (t_text.focus_set(), "break")[1])
        win.bind("<Control-Return>", save)
        win.bind("<Escape>", lambda e: win.destroy())
        on_title()
        on_text()
        win.update_idletasks()
        x = max(0, min(self.winfo_x() - win.winfo_width() - 10, self.winfo_screenwidth() - win.winfo_width()))
        win.geometry(f"+{x}+{max(0, self.winfo_y() + 40)}")
        e_title.focus_force()
        e_title.icursor("end")
        win._save, win._title_var, win._text = save, v_title, t_text  # для автотестов

    # ----- смена статуса
    def _prefetch_states(self, issue):
        """Пока пользователь открывает меню — заранее загружаем доступные переходы."""
        iid = issue.get("idReadable")
        token, cfg = unprotect(self.cfg["token_enc"]), dict(self.cfg)

        def work():
            try:
                res = fetch_state_options(cfg["base_url"], token, issue, cfg["state_field"])
            except YTError as ex:
                res = YTError(str(ex))
            except Exception as ex:  # noqa
                res = YTError(f"Ошибка: {ex}")
            self.q.put((lambda r: self._state_cache.__setitem__(iid, r), res))

        self._state_cache.pop(iid, None)
        threading.Thread(target=work, daemon=True).start()

    def _fill_state_menu(self):
        m = self.state_menu
        m.delete(0, "end")
        issue = self.menu_issue
        if not issue:
            return
        iid = issue.get("idReadable")
        opts = self._state_cache.get(iid)
        if opts is None:  # ещё не загрузилось — догружаем сразу (обычно доли секунды)
            self.configure(cursor="watch")
            self.update_idletasks()
            try:
                opts = fetch_state_options(self.cfg["base_url"], unprotect(self.cfg["token_enc"]),
                                           issue, self.cfg["state_field"])
            except YTError as ex:
                opts = ex
            finally:
                self.configure(cursor="")
            self._state_cache[iid] = opts
        if isinstance(opts, Exception):
            m.add_command(label=str(opts)[:80], state="disabled")
        elif not opts:
            m.add_command(label="Нет доступных переходов", state="disabled")
        else:
            for label, cmd in opts:
                m.add_command(label=label, command=lambda l=label, c=cmd, iss=issue: self.change_state(iss, l, c))

    def change_state(self, issue, label, command):
        self.change_field(issue, "статус", label, command)

    def change_field(self, issue, what, label, command):
        """Применить команду YouTrack (смена статуса/приоритета/спринта) в фоне и перечитать задачи."""
        iid = issue.get("idReadable")
        self.status_lbl.configure(text=f"{iid}: меняю {what} на «{label}»…", fg=BG_SUB)
        token, base = unprotect(self.cfg["token_enc"]), self.cfg["base_url"]

        def work():
            try:
                apply_command(base, token, issue, command)
                err = None
            except YTError as ex:
                err = str(ex)
            except Exception as ex:  # noqa
                err = f"Ошибка: {ex}"
            self.q.put((self._state_changed, (iid, what, label, err)))

        threading.Thread(target=work, daemon=True).start()

    def _state_changed(self, res):
        iid, what, label, err = res
        self._state_cache.pop(iid, None)
        if err:
            self.status_lbl.configure(text=f"{iid}: {what} не изменён", fg=ERR)
            messagebox.showerror(f"Смена: {what}", f"{iid}: не удалось изменить {what} на «{label}».\n\n{err}",
                                 parent=self)
            return
        self._status_note = (f"✓ {iid}: {what} → {label}", time.time())  # не затирать сразу после обновления
        self.status_lbl.configure(text=self._status_note[0], fg=BG_SUB)
        self.refresh()  # перечитать задачи (задача могла уйти из списка, если статус её закрывает)

    # ----- выпадающие списки на плашках карточки (левый клик по статусу / приоритету / спринту)
    FIELD_MENUS = {"a": ("state", "статус"), "p": ("priority", "приоритет"), "r": ("sprint", "спринт")}

    def open_field_menu(self, card_index, role):
        kind, what = self.FIELD_MENUS[role]
        issue = self.visible[card_index]["issue"]
        field_name = {"state": self.cfg["state_field"], "priority": self.cfg["priority_field"],
                      "sprint": self.cfg.get("sprint_field", "")}[kind]
        c = self.canvas
        box = c.bbox(f"{role}{card_index}")
        x = c.winfo_rootx() + int(box[0] - c.canvasx(0))
        y = c.winfo_rooty() + int(box[3] - c.canvasy(0)) + 2
        self.configure(cursor="watch")
        c.configure(cursor="watch")
        self.update_idletasks()
        try:
            data = fetch_field_options(self.cfg["base_url"], unprotect(self.cfg["token_enc"]), issue, field_name, kind)
        except YTError as ex:
            data = ex
        finally:
            self.configure(cursor="")
            c.configure(cursor="")
        m = tk.Menu(self, tearoff=0, bg=CARD, fg=TEXT, activebackground=CARD_HOVER, activeforeground=TEXT, bd=0,
                    font=(FONT, max(9, FS - 2)))
        self.field_menu = m  # для автотестов
        if isinstance(data, Exception):
            m.add_command(label=str(data)[:80], state="disabled")
        else:
            cur = ", ".join(data["current"]) or "не задан"
            head = {"state": f"Сейчас: {cur} — перевести в:", "priority": "Приоритет:",
                    "sprint": f"Спринт ({cur}):"}[kind]
            m.add_command(label=head, state="disabled")
            if not data["options"]:
                m.add_command(label="Нет доступных переходов" if kind == "state" else "Нет доступных значений",
                              state="disabled")
            for label, cmd, checked in data["options"]:
                if kind == "sprint" and checked:
                    text, done_label = f"✓ {label}   (убрать из спринта)", f"убрано из {label}"
                else:
                    text, done_label = ("✓ " if checked else "    ") + label, label
                if kind == "priority" and checked:  # текущий приоритет — ничего не меняем
                    m.add_command(label=text, state="disabled")
                    continue
                m.add_command(label=text, command=lambda c_=cmd, l_=done_label: self.change_field(issue, what, l_, c_))
        m.tk_popup(x, y)

    def _card_menu(self, e, issue):
        self.menu_issue = issue
        self.menu.entryconfigure(self.menu_plan_index, label="Убрать из плана на день" if self.in_plan(issue)
                                 else "Добавить в план на день")
        self._prefetch_states(issue)
        self.menu.tk_popup(e.x_root, e.y_root)

    # ----- настройки
    def open_settings(self):
        if getattr(self, "_settings", None) and self._settings.winfo_exists():
            self._settings.lift()
            return
        # окно настроек всегда в тёмной палитре, независимо от темы виджета
        BG, CARD, CARD_HOVER, TEXT, SUB = DLG_BG, DLG_CARD, DLG_HOVER, DLG_TEXT, DLG_SUB
        win = tk.Toplevel(self)
        self._settings = win
        win.title("Настройки YouTrack Todo")
        win.configure(bg=BG, padx=16, pady=12)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        try:  # тёмный заголовок окна
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
            v = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
        except Exception:
            pass

        def label(text, r, hint=None):
            tk.Label(win, text=text, bg=BG, fg=TEXT, font=(FONT, 9), anchor="w").grid(
                row=r, column=0, sticky="w", pady=(8, 0))
            if hint:
                tk.Label(win, text=hint, bg=BG, fg=SUB, font=(FONT, 8), anchor="w", justify="left").grid(
                    row=r + 2, column=0, sticky="w")

        def entry(r, value, show=None):
            e = tk.Entry(win, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT, 10),
                         width=48, show=show)
            e.insert(0, value)
            e.grid(row=r + 1, column=0, sticky="we", ipady=4)
            return e

        has_token = bool(unprotect(self.cfg["token_enc"]))
        label("Адрес YouTrack", 0, "например: https://company.youtrack.cloud")
        e_url = entry(0, self.cfg["base_url"])
        label("Постоянный токен (permanent token)", 3,
              "Профиль → Аккаунт и безопасность → Токены → Новый токен"
              + ("\nОставьте пустым, чтобы не менять сохранённый" if has_token else ""))
        e_tok = entry(3, "", show="•")
        if has_token:
            tk.Label(win, text="✓ токен сохранён", bg=BG, fg="#81c995", font=(FONT, 9)).grid(
                row=3, column=0, sticky="e", pady=(8, 0))
        label("Запрос (синтаксис поиска YouTrack)", 6, "например: Assignee: me #Unresolved sort by: Priority")
        e_q = entry(6, self.cfg["query"])

        fields = tk.Frame(win, bg=BG)
        fields.grid(row=9, column=0, sticky="we", pady=(8, 0))
        tk.Label(fields, text="Поле статуса", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        tk.Label(fields, text="Поле приоритета", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=0, column=1, sticky="w", padx=8)
        tk.Label(fields, text="Обновлять, мин", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=0, column=2, sticky="w")
        mk = lambda v, w: tk.Entry(fields, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                                   font=(FONT, 10), width=w)
        e_sf, e_pf, e_rm = mk("", 16), mk("", 16), mk("", 8)
        e_sf.insert(0, self.cfg["state_field"]); e_pf.insert(0, self.cfg["priority_field"])
        e_rm.insert(0, str(self.cfg["refresh_min"]))
        e_sf.grid(row=1, column=0, ipady=4, sticky="w")
        e_pf.grid(row=1, column=1, ipady=4, sticky="w", padx=8)
        e_rm.grid(row=1, column=2, ipady=4, sticky="w")

        opts = tk.Frame(win, bg=BG)
        opts.grid(row=10, column=0, sticky="we", pady=(12, 0))
        v_top = tk.BooleanVar(value=self.cfg["topmost"])
        v_auto = tk.BooleanVar(value=os.path.exists(STARTUP_LNK))
        cb = dict(bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG, activeforeground=TEXT, font=(FONT, 9))
        tk.Checkbutton(opts, text="Поверх всех окон", variable=v_top, **cb).pack(anchor="w")
        tk.Checkbutton(opts, text="Запускать вместе с Windows", variable=v_auto, **cb).pack(anchor="w")
        v_notify = tk.BooleanVar(value=bool(self.cfg.get("notify", True)))
        notify_row = tk.Frame(opts, bg=BG)
        notify_row.pack(anchor="w", fill="x")
        tk.Checkbutton(notify_row, text="Уведомления Windows о новых задачах и комментариях",
                       variable=v_notify, **cb).pack(side="left")
        test_toast = tk.Label(notify_row, text="проверить", bg=BG, fg=ACCENT_DLG, font=(FONT, 8, "underline"),
                              cursor="hand2")
        test_toast.pack(side="left", padx=(4, 0))
        test_toast.bind("<Button-1>", lambda e: show_toast(
            "YouTrack Widget", "Так будут выглядеть уведомления о новых задачах и комментариях.",
            self.cfg.get("base_url", ""), "Клик по уведомлению откроет задачу в браузере"))
        v_tray = tk.BooleanVar(value=bool(self.cfg.get("tray", True)))
        tk.Checkbutton(opts, text="Значок в трее со счётчиком комментариев (✕ прячет виджет в трей)",
                       variable=v_tray, **cb).pack(anchor="w")
        hk_row = tk.Frame(opts, bg=BG)
        hk_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(hk_row, text="Горячая клавиша", bg=BG, fg=TEXT, font=(FONT, 9)).pack(side="left")
        e_hk = tk.Entry(hk_row, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", font=(FONT, 10), width=16)
        e_hk.insert(0, self.cfg.get("hotkey", ""))
        e_hk.pack(side="left", padx=8, ipady=3)
        tk.Label(hk_row, text="например Win+Shift+Y, Ctrl+Alt+T; пусто — выкл.", bg=BG, fg=SUB,
                 font=(FONT, 8)).pack(side="left")
        alpha_row = tk.Frame(opts, bg=BG)
        alpha_row.pack(anchor="w", fill="x", pady=(4, 0))
        tk.Label(alpha_row, text="Прозрачность", bg=BG, fg=TEXT, font=(FONT, 9)).pack(side="left")
        v_alpha = tk.DoubleVar(value=float(self.cfg["alpha"]))
        tk.Scale(alpha_row, from_=0.5, to=1.0, resolution=0.01, orient="horizontal", variable=v_alpha,
                 bg=BG, fg=TEXT, troughcolor=CARD, highlightthickness=0, length=200, showvalue=False,
                 command=lambda v: self.attributes("-alpha", float(v))).pack(side="left", padx=8)

        # ----- Оформление: цвета и шрифт (применяются сразу для предпросмотра)
        look = tk.Frame(opts, bg=BG)
        look.pack(anchor="w", fill="x", pady=(10, 0))
        tk.Label(look, text="Оформление", bg=BG, fg=TEXT, font=(FONT, 9, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        v_bg = tk.StringVar(value=self.cfg["bg_color"] if _valid_color(self.cfg["bg_color"]) else DEFAULTS["bg_color"])
        v_card = tk.StringVar(value=self.cfg["card_color"] if _valid_color(self.cfg["card_color"])
                              else DEFAULTS["card_color"])
        v_fs = tk.IntVar(value=FS)

        def preview(*_):
            try:
                fs = int(v_fs.get())
            except (tk.TclError, ValueError):
                return
            apply_theme(v_bg.get(), v_card.get(), fs)
            self.rebuild()
            sw_bg.configure(bg=v_bg.get(), activebackground=v_bg.get())
            sw_card.configure(bg=v_card.get(), activebackground=v_card.get())
            win.lift()

        def pick(var, title):
            rgb, hexc = colorchooser.askcolor(color=var.get(), parent=win, title=title)
            if hexc:
                var.set(hexc.lower())
                preview()

        def swatch(var, title, col):
            border = tk.Frame(look, bg="#8a8a8a", padx=1, pady=1)  # рамка, чтобы тёмный цвет был виден
            border.grid(row=1, column=col, sticky="w", padx=(8, 20), pady=2)
            s = tk.Button(border, bg=var.get(), activebackground=var.get(), width=4, relief="flat", bd=0,
                          cursor="hand2", command=lambda: pick(var, title))
            s.pack()
            return s

        tk.Label(look, text="Фон окна", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=1, column=0, sticky="w")
        sw_bg = swatch(v_bg, "Цвет фона окна", 1)
        tk.Label(look, text="Фон карточек", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=1, column=2, sticky="w")
        sw_card = swatch(v_card, "Цвет фона карточек", 3)

        tk.Label(look, text="Размер шрифта", bg=BG, fg=TEXT, font=(FONT, 9)).grid(row=2, column=0, sticky="w")
        sp = tk.Spinbox(look, from_=9, to=18, textvariable=v_fs, width=4, command=preview, bg=CARD, fg=TEXT,
                        insertbackground=TEXT, buttonbackground=CARD, relief="flat", font=(FONT, 10))
        sp.grid(row=2, column=1, sticky="w", padx=(8, 20), pady=4)
        sp.bind("<KeyRelease>", preview)

        presets = tk.Frame(look, bg=BG)
        presets.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        tk.Label(presets, text="Готовые:", bg=BG, fg=SUB, font=(FONT, 8)).pack(side="left", padx=(0, 6))

        def use_preset(bg, card):
            v_bg.set(bg)
            v_card.set(card)
            preview()

        for name, pbg, pcard in (("Тёмная", "#1f1f1f", "#2b2b2b"), ("Графит", "#151a21", "#232b36"),
                                 ("Синяя", "#0f2742", "#173a5e"), ("Светлая", "#eef0f3", "#ffffff"),
                                 ("Бежевая", "#efe9df", "#fbf7f0")):
            b = tk.Label(presets, text=name, bg=pcard, fg="#e8e8e8" if _lum(pcard) < 0.5 else "#1b1b1b",
                         font=(FONT, 8), padx=6, pady=2, cursor="hand2", highlightthickness=3,
                         highlightbackground=pbg)
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda e, x=pbg, y=pcard: use_preset(x, y))

        test_lbl = tk.Label(win, text="", bg=BG, fg=SUB, font=(FONT, 9), anchor="w", justify="left", wraplength=380)
        test_lbl.grid(row=11, column=0, sticky="we", pady=(10, 0))

        def current_token():
            return e_tok.get().strip() or unprotect(self.cfg["token_enc"])

        def test():
            url, tok, q = e_url.get().strip(), current_token(), e_q.get().strip()
            if not url or not tok:
                test_lbl.configure(text="Укажите адрес и токен", fg=ERR)
                return
            test_lbl.configure(text="Проверяю…", fg=SUB)

            def work():
                try:
                    data = fetch_issues(url, tok, q, 100)
                    res = (f"✓ Подключено. Найдено задач по запросу: {len(data)}"
                           + (" (показаны первые 100)" if len(data) >= 100 else ""), "#81c995")
                except YTError as e:
                    res = (str(e), ERR)
                self.q.put((lambda r: test_lbl.winfo_exists() and test_lbl.configure(text=r[0], fg=r[1]), res))

            threading.Thread(target=work, daemon=True).start()

        def save():
            url = e_url.get().strip()
            if url and not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            if e_tok.get().strip():
                self.cfg["token_enc"] = protect(e_tok.get().strip())
            try:
                rm = max(1, int(e_rm.get().strip()))
            except ValueError:
                rm = 5
            self.cfg.update(base_url=url, query=e_q.get().strip() or DEFAULTS["query"],
                            state_field=e_sf.get().strip() or "State",
                            priority_field=e_pf.get().strip() or "Priority",
                            refresh_min=rm, topmost=v_top.get(), alpha=round(v_alpha.get(), 2),
                            bg_color=v_bg.get(), card_color=v_card.get(), font_size=font_size(),
                            notify=v_notify.get(), tray=v_tray.get())
            hk = e_hk.get().strip()
            if hk and not parse_hotkey(hk):
                messagebox.showwarning("Горячая клавиша", f"Не понял сочетание «{hk}».\n"
                                       "Пример: Win+Shift+Y или Ctrl+Alt+F9.", parent=win)
                return
            self.cfg["hotkey"] = hk
            save_cfg(self.cfg)
            apply_theme(self.cfg["bg_color"], self.cfg["card_color"], self.cfg["font_size"])
            self.rebuild()
            self.setup_tray()
            if not self.setup_hotkey():
                messagebox.showwarning("Горячая клавиша", f"Сочетание {hk} уже занято другой программой "
                                       "или Windows. Выберите другое.", parent=win)
            self.attributes("-topmost", self.cfg["topmost"])
            self._btn_leave(self.pin_btn)
            try:
                set_autostart(v_auto.get())
            except Exception as e:
                messagebox.showwarning("Автозапуск", f"Не удалось настроить автозапуск:\n{e}", parent=win)
            win.destroy()
            self.issues = []
            self.last_ok = None
            self._data_sig = None
            self._notified = None   # после смены запроса не считаем «старые» задачи новыми
            self._known_ids = None
            self.refresh()

        def font_size():
            try:
                return max(9, min(18, int(v_fs.get())))
            except (tk.TclError, ValueError):
                return self.cfg["font_size"]

        def cancel():  # откатить предпросмотр
            self.attributes("-alpha", float(self.cfg["alpha"]))
            win.destroy()
            apply_theme(self.cfg["bg_color"], self.cfg["card_color"], self.cfg["font_size"])
            self.rebuild()

        btns = tk.Frame(win, bg=BG)
        btns.grid(row=12, column=0, sticky="e", pady=(12, 0))
        bstyle = dict(relief="flat", font=(FONT, 9), padx=12, pady=4, cursor="hand2", bd=0)
        tk.Button(btns, text="Проверить", command=test, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
                  activeforeground=TEXT, **bstyle).pack(side="left", padx=4)
        tk.Button(btns, text="Отмена", command=cancel, bg=CARD, fg=TEXT, activebackground=CARD_HOVER,
                  activeforeground=TEXT, **bstyle).pack(side="left", padx=4)
        tk.Button(btns, text="Сохранить", command=save, bg="#0067c0", fg="white", activebackground="#1975c5",
                  activeforeground="white", **bstyle).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", cancel)
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: cancel())
        win.update_idletasks()
        x = max(0, min(self.winfo_x() - win.winfo_width() - 10, self.winfo_screenwidth() - win.winfo_width()))
        win.geometry(f"+{x}+{max(0, self.winfo_y())}")
        e_url.focus_set()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # чёткий шрифт на HiDPI
    except Exception:
        pass
    q = queue.Queue()
    if not acquire_single_instance(q):
        return  # уже запущен — попросили его показаться
    load_icon_font()
    register_toast_app()
    Widget(q).mainloop()


if __name__ == "__main__":
    main()
