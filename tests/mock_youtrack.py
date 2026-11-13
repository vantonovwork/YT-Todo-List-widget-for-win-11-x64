"""
Тестовый сервер, имитирующий REST API YouTrack. Запускается в потоке внутри тестов (start()) или отдельно:
    python mock_youtrack.py 8766

Управление (POST): /test/reset, /test/comment, /test/my-comment, /test/new-issue, /test/many-comments,
/test/mode?m=ok|401|403|404|400|500|badjson|slow, /test/bulk?n=60
"""
import datetime as dt
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

TOKEN = "perm:test"


def ms(days):
    d = dt.date.today() + dt.timedelta(days=days)
    return int(dt.datetime(d.year, d.month, d.day, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)


def st(v, typ="StateMachineIssueCustomField"):
    return {"id": "f-state", "name": "Статус задачи", "$type": typ, "value": {"name": v}}


def pr(v, bg=None):
    return {"id": "f-prio", "name": "Приоритет", "$type": "SingleEnumIssueCustomField",
            "value": {"name": v, **({"color": {"background": bg, "foreground": "#fff"}} if bg else {})}}


def due(days):
    return {"id": "f-due", "name": "Дата окончания", "$type": "DateIssueCustomField",
            "value": None if days is None else ms(days)}


def sp(*vals):
    return {"id": "f-sprint", "name": "Спринт", "$type": "MultiVersionIssueCustomField", "value": list(vals)}


def cm(t, who="ilya", name="Илья Гаврилов", text="Посмотрел, есть вопросы по **маппингу** полей"):
    return {"created": t, "deleted": False, "text": text, "author": {"login": who, "fullName": name}}


CUR = {"name": "26.22", "archived": False, "released": False, "releaseDate": ms(10)}
OLD = {"name": "26.20", "archived": True, "released": False, "releaseDate": ms(-20)}
NEXT = {"name": "тестовый спринт 24", "archived": False, "released": False, "releaseDate": ms(24)}
SPRINTS = [OLD, CUR, NEXT]
PRIORITIES = ["Неотложный", "Критический", "Высокий", "Средний", "Низкий"]
EVENTS = {"Открыта": [("-->", "Ждет БА"), ("--->", "Проработка БА"), ("отклонена", "Отклонена")],
          "Анализ": [("готово", "Готова")], "Ждет БА": [("в-работу", "В работе")]}
ENUM_STATES = ["Open", "In Progress", "Fixed", "Old (archived)"]


def initial():
    return [
        {"id": "2-1", "idReadable": "OE-1", "summary": "Просроченная задача по выгрузке цен",
         "customFields": [st("Открыта"), pr("Критический", "#e30000"), due(-2), sp(CUR)], "comments": [cm(1000)]},
        {"id": "2-2", "idReadable": "OE-2", "summary": "Срок сегодня: интеграция трансферов",
         "customFields": [st("Анализ"), pr("Высокий", "#ff7a00"), due(0), sp(OLD, CUR)], "comments": []},
        {"id": "2-3", "idReadable": "OE-3", "summary": "Срок завтра: отчёт DOTW",
         "customFields": [st("Ждет БА"), pr("Средний"), due(1), sp(OLD)], "comments": []},
        {"id": "2-4", "idReadable": "OE-4", "summary": "Срок через неделю",
         "customFields": [st("Ждет БА"), pr("Низкий"), due(7), sp()], "comments": []},
        {"id": "2-5", "idReadable": "OE-5", "summary": "Без срока и без приоритета",
         "customFields": [st("Открыта"), {"id": "f-prio", "name": "Приоритет", "$type": "SingleEnumIssueCustomField",
                                          "value": None}, due(None), sp()], "comments": []},
        {"id": "2-7", "idReadable": "OE-7", "summary": "Проект с обычным полем состояния (не workflow)",
         "customFields": [st("Open", "StateIssueCustomField"), pr("Средний"), due(None), sp()], "comments": []},
    ]


class State:
    issues = initial()
    mode = "ok"
    commands = []
    requests = []


def bulk(n):
    import random
    rnd = random.Random(1)
    words = "интеграция выгрузка цены отчёт трансферы бронирование отмена поставщик Expedia DOTW API промо".split()
    return [{"id": f"3-{i}", "idReadable": f"OE-{1000 + i}",
             "summary": " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 16))).capitalize(),
             "customFields": [st(rnd.choice(["Открыта", "Анализ", "Ждет БА"])),
                              pr(rnd.choice(["Критический", "Высокий", "Средний", "Низкий"])),
                              due(rnd.choice([None, -1, 0, 3])), sp()],
             "comments": [cm(1000 + k) for k in range(rnd.randint(0, 4))]} for i in range(n)]


class Handler(BaseHTTPRequestHandler):
    def _send(self, data, code=200, raw=None):
        body = raw if raw is not None else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _auth_and_mode(self):
        State.requests.append(self.path)
        m = State.mode
        if self.headers.get("Authorization") != f"Bearer {TOKEN}" or m == "401":
            self._send({"error_description": "Unauthorized"}, 401)
            return False
        if m in ("403", "404", "500"):
            self._send({"error_description": f"mode {m}"}, int(m))
            return False
        if m == "400":
            self._send({"error_description": "Unknown field 'xxx'"}, 400)
            return False
        if m == "badjson":
            self._send(None, 200, b"<html>login page</html>")
            return False
        if m == "slow":
            time.sleep(30)
        return True

    def do_GET(self):
        p = urlparse(self.path).path
        if p.startswith("/test/"):
            if p == "/test/commands":
                return self._send(State.commands)
            return self._send(State.requests)
        if not self._auth_and_mode():
            return
        if p == "/api/users/me":
            return self._send({"login": "me"})
        if p == "/api/issues":
            return self._send(State.issues)
        parts = p.split("/")
        if p.startswith("/api/issues/") and "/customFields/" in p:
            issue = next(i for i in State.issues if i["id"] == parts[3])
            fid = parts[5]
            if fid == "f-prio":
                return self._send({"possibleEvents": [], "projectCustomField": {"bundle": {"values": [
                    {"name": v, "archived": False} for v in PRIORITIES]}}})
            if fid == "f-sprint":
                return self._send({"possibleEvents": [], "projectCustomField": {"bundle": {"values": SPRINTS}}})
            f = issue["customFields"][0]
            if f["$type"] == "StateMachineIssueCustomField":
                cur = f["value"]["name"]
                return self._send({"possibleEvents": [{"id": e, "presentation": n, "$type": "Event"}
                                                      for e, n in EVENTS.get(cur, [])]})
            return self._send({"possibleEvents": [], "projectCustomField": {"bundle": {"values": [
                {"name": v, "archived": "archived" in v} for v in ENUM_STATES]}}})
        if p.startswith("/api/issues/") and len(parts) == 4:
            return self._send({"description": "Нужно **обновить** выгрузку.\n\nШаги:\n1. Проверить "
                                              "[документацию](http://x)\n```\nкод\n```\nГотово."})
        self._send({}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        now = int(time.time() * 1000)
        if p == "/test/reset":
            State.issues, State.mode, State.commands, State.requests = initial(), "ok", [], []
        elif p == "/test/mode":
            State.mode = parse_qs(u.query).get("m", ["ok"])[0]
        elif p == "/test/bulk":
            State.issues = bulk(int(parse_qs(u.query).get("n", ["60"])[0]))
        elif p == "/test/comment":
            State.issues[1]["comments"].append(cm(now, text="Нужна проверка на тесте, см. [лог](http://x)"))
        elif p == "/test/my-comment":
            State.issues[2]["comments"].append(cm(now, who="me", name="Я"))
        elif p == "/test/many-comments":
            for i in State.issues[:5]:
                i["comments"].append(cm(now))
        elif p == "/test/new-issue":
            State.issues.append({"id": "2-9", "idReadable": "OE-9", "summary": "Новая задача: проверить промо",
                                 "customFields": [st("Открыта"), pr("Высокий", "#ff7a00"), due(3), sp()],
                                 "comments": [cm(500)]})
        elif p == "/api/commands":
            if not self._auth_and_mode():
                return
            State.commands.append(body)
            issue = next(i for i in State.issues if i["id"] == body["issues"][0]["id"])
            fields = {f["name"]: f for f in issue["customFields"]}
            q = body["query"]
            if "{" in q:  # как настоящий YouTrack пользователя: значения в фигурных скобках не принимаются
                return self._send({"error_description": f"Ожидается значение без скобок: {q}"}, 400)
            if q.startswith("Статус задачи "):
                f, val = fields["Статус задачи"], q.split("Статус задачи ", 1)[1]
                if f["$type"] == "StateMachineIssueCustomField":
                    target = dict(EVENTS.get(f["value"]["name"], [])).get(val)
                else:
                    target = val if val in ENUM_STATES and "archived" not in val else None
                if not target:
                    return self._send({"error_description": f"Переход недоступен: {q}"}, 400)
                f["value"]["name"] = target
            elif q.startswith("Приоритет "):
                val = q.split("Приоритет ", 1)[1]
                if val not in PRIORITIES:
                    return self._send({"error_description": f"Нет значения: {val}"}, 400)
                fields["Приоритет"]["value"] = {"name": val}
            else:  # «remove Спринт X add Спринт Y»
                import re
                ops = re.findall(r"(remove|add) Спринт (.+?)(?= (?:remove|add) Спринт |$)", q)
                if not ops:
                    return self._send({"error_description": f"Неизвестная команда: {q}"}, 400)
                f = fields["Спринт"]
                for op, name in ops:
                    known = next((s for s in SPRINTS if s["name"] == name), None)
                    if not known:
                        return self._send({"error_description": f"Нет спринта: {name}"}, 400)
                    if op == "remove":
                        f["value"] = [s for s in f["value"] if s["name"] != name]
                    elif all(s["name"] != name for s in f["value"]):
                        f["value"].append(dict(known))
        elif p.startswith("/api/issues/") and p.endswith("/comments"):
            if not self._auth_and_mode():
                return
            issue = next((i for i in State.issues if i["id"] == p.split("/")[3]), None)
            if issue is None:
                return self._send({"error_description": "Issue not found"}, 404)
            text = (body.get("text") or "").strip()
            if not text:
                return self._send({"error_description": "Comment text is empty"}, 400)
            c = {"id": f"4-{len(State.commands) + len(issue['comments']) + 1}", "created": now, "deleted": False,
                 "text": text, "author": {"login": "me", "fullName": "Я"}}
            issue["comments"].append(c)
            State.commands.append({"comment": text, "issue": issue["idReadable"]})
            return self._send(c)
        else:
            return self._send({}, 404)
        self._send({})

    def log_message(self, *a):
        pass


def start(port=0):
    """Запустить сервер в фоновом потоке; вернуть (server, base_url)."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def control(base, path):
    import urllib.request
    urllib.request.urlopen(urllib.request.Request(base + path, data=b"{}", method="POST"), timeout=5).read()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8766), Handler).serve_forever()
