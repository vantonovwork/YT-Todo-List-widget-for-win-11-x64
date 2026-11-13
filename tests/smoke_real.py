"""
Проверка на настоящем YouTrack пользователя — ТОЛЬКО ЧТЕНИЕ, задачи не меняются.
Берёт адрес и токен из настроек установленного виджета (%APPDATA%\\YouTrackWidget\\config.json).
"""
import importlib.util
import json
import os
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("ytw", os.path.join(ROOT, "youtrack_widget.pyw"))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)

ok_count = fail_count = 0


def check(name, cond, detail=""):
    global ok_count, fail_count
    if cond:
        ok_count += 1
        print(f"  ✓ {name}" + (f" — {detail}" if detail else ""))
    else:
        fail_count += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


cfg = w.load_cfg()
token = w.unprotect(cfg["token_enc"])
print(f"YouTrack: {cfg['base_url']}\nЗапрос: {cfg['query']}\n")
check("настройки найдены, токен расшифровывается", bool(cfg["base_url"] and token))

t = time.perf_counter()
me = w.fetch_me(cfg["base_url"], token)
check("токен действителен (users/me)", bool(me))
issues = w.fetch_issues(cfg["base_url"], token, cfg["query"], int(cfg["max_issues"]))
dt = (time.perf_counter() - t) * 1000
check("задачи загружаются", len(issues) > 0, f"{len(issues)} задач за {dt:.0f} мс")
check("все задачи назначены на вас", all(
    any(f.get("name") == "Assignee" and (f.get("value") or {}).get("login") == me for f in i["customFields"])
    for i in issues) if "Assignee" in cfg["query"] else True)
check("статус найден у всех задач", all(w.field_value(i, cfg["state_field"], "state")[0] for i in issues))
n_prio = sum(1 for i in issues if w.field_value(i, cfg["priority_field"], "priority")[0])
check("приоритет найден", n_prio > 0, f"у {n_prio} из {len(issues)}")
n_due = sum(1 for i in issues if w.due_date(i, cfg.get("due_field", "")))
n_sprint = sum(1 for i in issues if w.sprints_of(i, cfg.get("sprint_field", "")))
print(f"  · срок задан у {n_due}, спринт — у {n_sprint} задач")
n_comments = sum(len(w.live_comments(i)) for i in issues)
check("комментарии загружаются с автором и текстом", all(
    c.get("author") and "text" in c for i in issues for c in w.live_comments(i)), f"{n_comments} комментариев")

for issue in issues[:3]:
    opts = w.fetch_state_options(cfg["base_url"], token, issue, cfg["state_field"])
    state = w.field_value(issue, cfg["state_field"], "state")[0]
    check(f"{issue['idReadable']} [{state}]: переходы статуса", isinstance(opts, list),
          ", ".join(label for label, _ in opts) or "нет переходов")
    if opts:  # команду только проверяем через «подсказку» — YouTrack её разбирает, но НЕ выполняет
        body = json.dumps({"query": opts[0][1], "caret": len(opts[0][1]), "issues": [{"id": issue["id"]}]}).encode()
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/api/commands/assist?fields=commands(error,description)",
                                     data=body, method="POST", headers={"Authorization": f"Bearer {token}",
                                                                         "Content-Type": "application/json"})
        cmds = json.load(urllib.request.urlopen(req, timeout=20)).get("commands", [])
        check(f"{issue['idReadable']}: команда «{opts[0][1]}» корректна (без выполнения)",
              cmds and not any(c.get("error") for c in cmds))

desc = w.fetch_description(cfg["base_url"], token, issues[0])
check("описание задачи загружается", isinstance(desc, str), f"{len(desc)} символов у {issues[0]['idReadable']}")
check("горячая клавиша из настроек корректна", not cfg.get("hotkey") or w.parse_hotkey(cfg["hotkey"]) is not None,
      cfg.get("hotkey") or "выключена")

print(f"\nИтог: {ok_count} проверок пройдено, {fail_count} с ошибками")
sys.exit(1 if fail_count else 0)
