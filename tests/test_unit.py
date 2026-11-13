"""Модульные тесты: функции без интерфейса."""
import datetime as dt
import json
import os
import struct
import unittest

from common import w, SANDBOX
import mock_youtrack as mock


def issue_with(*fields, comments=None):
    return {"id": "1-1", "idReadable": "T-1", "summary": "x", "customFields": list(fields), "comments": comments or []}


class TestTokenAndConfig(unittest.TestCase):
    def test_dpapi_roundtrip(self):
        for s in ("perm:abc", "perm:" + "x" * 300, "токен с юникодом"):
            enc = w.protect(s)
            self.assertNotIn(s, enc)
            self.assertEqual(w.unprotect(enc), s)

    def test_unprotect_garbage_and_empty(self):
        self.assertEqual(w.unprotect(""), "")
        self.assertEqual(w.unprotect("не base64 !!!"), "")
        self.assertEqual(w.protect(""), "")

    def test_config_defaults_merge_and_atomic_save(self):
        if os.path.exists(w.CFG_PATH):
            os.remove(w.CFG_PATH)
        self.assertEqual(w.load_cfg()["query"], w.DEFAULTS["query"])
        cfg = w.load_cfg()
        cfg["query"] = "Assignee: me"
        w.save_cfg(cfg)
        self.assertEqual(w.load_cfg()["query"], "Assignee: me")
        self.assertFalse(os.path.exists(w.CFG_PATH + ".tmp"))
        with open(w.CFG_PATH, "w", encoding="utf-8") as f:
            f.write("{broken json")
        self.assertEqual(w.load_cfg()["query"], w.DEFAULTS["query"])  # битый файл → настройки по умолчанию


class TestFields(unittest.TestCase):
    def test_field_by_name_case_insensitive(self):
        i = issue_with({"name": "State", "$type": "StateIssueCustomField", "value": {"name": "Open"}})
        self.assertEqual(w.field_value(i, "state", "state")[0], "Open")

    def test_state_autodetect_by_type(self):
        i = issue_with({"name": "Статус задачи", "$type": "StateMachineIssueCustomField", "value": {"name": "Анализ"}})
        self.assertEqual(w.field_value(i, "State", "state")[0], "Анализ")

    def test_priority_autodetect_by_name(self):
        i = issue_with({"name": "Приоритет", "$type": "SingleEnumIssueCustomField",
                        "value": {"name": "Высокий", "color": {"background": "#ff7a00", "foreground": "#fff"}}})
        text, color = w.field_value(i, "Priority", "priority")
        self.assertEqual(text, "Высокий")
        self.assertEqual(color["background"], "#ff7a00")

    def test_value_shapes(self):
        self.assertEqual(w.field_value(issue_with({"name": "P", "value": None}), "P"), (None, None))
        self.assertEqual(w.field_value(issue_with({"name": "P", "value": [{"name": "a"}, {"name": "b"}]}), "P")[0], "a, b")
        self.assertEqual(w.field_value(issue_with({"name": "P", "value": 5}), "P")[0], "5")
        self.assertEqual(w.field_value(issue_with({"name": "P", "value": {"fullName": "Иван"}}), "P")[0], "Иван")
        self.assertEqual(w.field_value(issue_with(), "P", "priority"), (None, None))

    def test_chip_colors(self):
        w.apply_theme("#1f1f1f", "#2b2b2b", 12)
        self.assertEqual(w.chip_colors("x", {"background": "#ffffff", "foreground": "#000"}), ("#3a3a3a", "#d6d6d6"))
        self.assertEqual(w.chip_colors("x", {"background": "#e30000", "foreground": "#fff"})[0], "#e30000")
        self.assertEqual(w.chip_colors("Критический", None)[0], "#d32f2f")
        self.assertEqual(w.chip_colors("x", {"background": "zzz"}), ("#3a3a3a", "#d6d6d6"))
        w.apply_theme("#eef0f3", "#ffffff", 12)
        self.assertEqual(w.chip_colors("x", None), ("#e3e5e8", "#2b2b2b"))  # на светлых карточках — светлые плашки
        w.apply_theme("#1f1f1f", "#2b2b2b", 12)


class TestDueAndSprint(unittest.TestCase):
    def due_issue(self, days, name="Дата окончания", typ="DateIssueCustomField"):
        return issue_with({"name": name, "$type": typ, "value": None if days is None else mock.ms(days)})

    def test_due_labels(self):
        cases = {-3: ("просрочено", True), -1: ("просрочено", True), 0: ("сегодня", True),
                 1: ("завтра", True), 2: ("до ", False), 30: ("до ", False)}
        for days, (start, hot) in cases.items():
            info = w.due_info(w.due_date(self.due_issue(days), ""))
            self.assertTrue(info[0].startswith(start), (days, info))
            self.assertEqual(info[3], hot, days)

    def test_due_missing_and_other_year(self):
        self.assertIsNone(w.due_date(self.due_issue(None), ""))
        self.assertIsNone(w.due_date(issue_with(), ""))
        self.assertIsNone(w.due_date(self.due_issue(0, name="Дата начала"), ""))  # «начала» — не срок
        far = dt.date.today().replace(year=dt.date.today().year + 1)
        self.assertIn(f".{far:%y}", w.due_info(far)[0])

    def test_due_explicit_field(self):
        i = self.due_issue(0, name="Deadline X", typ="SimpleIssueCustomField")
        self.assertIsNone(w.due_date(i, ""))
        self.assertIsNotNone(w.due_date(i, "Deadline X"))

    def test_sprints(self):
        i = issue_with(mock.sp(mock.OLD, mock.CUR))
        self.assertEqual(w.sprints_of(i, ""), [("26.22", True), ("26.20", False)])
        self.assertEqual(w.sprints_of(issue_with(mock.sp()), ""), [])
        self.assertEqual(w.sprints_of(issue_with(), ""), [])
        int_field = issue_with({"name": "Дата добавления в спринт", "$type": "SimpleIssueCustomField", "value": 5})
        self.assertEqual(w.sprints_of(int_field, ""), [])  # поле-число со словом «спринт» не путаем


class TestText(unittest.TestCase):
    def test_clean_markdown(self):
        s = w.clean_markdown("**Жирный** [ссылка](http://x) ![img](a.png)\n\n\n```\ncode\n```\n> цитата", 500)
        self.assertEqual(s, "Жирный ссылка [картинка]\n[код]\nцитата")
        self.assertTrue(w.clean_markdown("а" * 50, 10).endswith("…"))
        self.assertEqual(len(w.clean_markdown("а" * 50, 10)), 10)
        self.assertEqual(w.clean_markdown(None, 10), "")

    def test_comment_snippet(self):
        c = {"text": "Смотри [лог](http://x)\n**важно**"}
        self.assertEqual(w.comment_snippet(c), "Смотри лог важно")


class TestHotkey(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(w.parse_hotkey("Win+Shift+Y"), (0x8 | 0x4, ord("Y")))
        self.assertEqual(w.parse_hotkey("ctrl + alt + f9"), (0x2 | 0x1, 0x78))
        self.assertEqual(w.parse_hotkey("Ctrl+5"), (0x2, ord("5")))
        for bad in ("", "Y", "Hyper+Y", "Win+Shift+Ж", "Ctrl+F13", "Ctrl+Enter"):
            self.assertIsNone(w.parse_hotkey(bad), bad)


class TestPlanStorage(unittest.TestCase):
    def setUp(self):
        os.makedirs(w.CFG_DIR, exist_ok=True)

    def test_legacy_list_format(self):
        with open(w.PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(["A", "B", 3], f)
        self.assertEqual(w.load_plan(), (["A", "B"], set()))

    def test_done_kept_today_dropped_tomorrow(self):
        w.save_plan(["A", "B"], {"A"})
        self.assertEqual(w.load_plan(), (["A", "B"], {"A"}))
        with open(w.PLAN_PATH, encoding="utf-8") as f:
            data = json.load(f)
        data["done_date"] = "2000-01-01"
        with open(w.PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(w.load_plan(), (["A", "B"], set()))

    def test_urgent_storage_and_limits(self):
        w.save_urgent([{"id": "U-1", "title": "  Т" * 60, "text": "x" * 1500, "created": 5, "done": 1},
                       {"id": "U-2", "title": "   ", "text": "пустое название — отбрасывается"},
                       {"title": "без id"}, "мусор"])
        items = w.load_urgent()
        self.assertEqual(len(items), 1)
        self.assertEqual(len(items[0]["title"]), 100)
        self.assertEqual(len(items[0]["text"]), 1000)
        self.assertIs(items[0]["done"], True)
        with open(w.URGENT_PATH, "w", encoding="utf-8") as f:
            f.write("not json")
        self.assertEqual(w.load_urgent(), [])

    def test_corrupted(self):
        with open(w.PLAN_PATH, "w", encoding="utf-8") as f:
            f.write("{{{")
        self.assertEqual(w.load_plan(), ([], set()))


class TestIcons(unittest.TestCase):
    def test_tray_pixels(self):
        for size in (16, 20, 24, 32, 40):
            base = w.tray_pixels(size, 0)
            self.assertEqual(len(base), size * size * 4)
            for n in (1, 9, 42, 150):
                px = w.tray_pixels(size, n)
                self.assertEqual(len(px), size * size * 4)
                self.assertNotEqual(px, base)  # кружок с числом действительно нарисован

    def test_make_icon(self):
        import make_icon
        png = make_icon.render(32)
        self.assertTrue(png.startswith(b"\x89PNG"))
        path = os.path.join(SANDBOX, "t.ico")
        make_icon.main(path)
        with open(path, "rb") as f:
            reserved, typ, count = struct.unpack("<HHH", f.read(6))
        self.assertEqual((reserved, typ, count), (0, 1, 6))

    def test_theme(self):
        w.apply_theme("#ffffff", "#ffffff", 50)
        self.assertEqual(w.TEXT, "#1b1b1b")
        self.assertEqual(w.FS, 20)  # размер шрифта ограничен
        w.apply_theme("bad", "#zzzzzz", "x")
        self.assertEqual((w.BG, w.CARD, w.FS), (w.DEFAULTS["bg_color"], w.DEFAULTS["card_color"], w.DEFAULTS["font_size"]))


class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = mock.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        mock.control(self.base, "/test/reset")

    def test_fetch_ok(self):
        data = w.fetch_issues(self.base, mock.TOKEN, "q", 10)
        self.assertEqual(len(data), 6)
        self.assertEqual(w.fetch_me(self.base, mock.TOKEN), "me")

    def test_error_messages(self):
        expect = {"401": "Доступ запрещён", "403": "Доступ запрещён", "404": "Не найдено",
                  "400": "Ошибка в запросе: Unknown field", "500": "Ошибка сервера: HTTP 500", "badjson": "не JSON"}
        for mode, text in expect.items():
            mock.control(self.base, f"/test/mode?m={mode}")
            with self.assertRaises(w.YTError) as ctx:
                w.fetch_issues(self.base, mock.TOKEN, "q", 10)
            self.assertIn(text, str(ctx.exception), mode)
        mock.control(self.base, "/test/mode?m=ok")
        with self.assertRaises(w.YTError) as ctx:
            w.fetch_issues(self.base, "perm:wrong", "q", 10)
        self.assertIn("токен", str(ctx.exception))

    def test_network_errors(self):
        with self.assertRaises(w.YTError) as ctx:
            w.fetch_issues("http://127.0.0.1:1", mock.TOKEN, "q", 1)
        self.assertIn("Нет соединения", str(ctx.exception))
        with self.assertRaises(w.YTError) as ctx:
            w.fetch_issues("https://no-such-host.invalid", mock.TOKEN, "q", 1)
        self.assertIn("не найден", str(ctx.exception))

    def test_state_options_and_commands(self):
        issues = {i["idReadable"]: i for i in w.fetch_issues(self.base, mock.TOKEN, "q", 50)}
        opts = w.fetch_state_options(self.base, mock.TOKEN, issues["OE-1"], "State")
        self.assertEqual([l for l, _ in opts], ["Ждет БА", "Проработка БА", "Отклонена"])
        w.apply_command(self.base, mock.TOKEN, issues["OE-1"], opts[0][1])
        new = {i["idReadable"]: i for i in w.fetch_issues(self.base, mock.TOKEN, "q", 50)}
        self.assertEqual(w.field_value(new["OE-1"], "State", "state")[0], "Ждет БА")
        # обычное поле (не workflow): все значения, кроме текущего и архивных; команда с фигурными скобками
        enum = w.fetch_state_options(self.base, mock.TOKEN, issues["OE-7"], "State")
        self.assertEqual(enum, [("In Progress", "Статус задачи In Progress"), ("Fixed", "Статус задачи Fixed")],
                         "значения без фигурных скобок — так их принимает YouTrack")
        w.apply_command(self.base, mock.TOKEN, issues["OE-7"], enum[0][1])
        # недопустимый переход → понятная ошибка
        with self.assertRaises(w.YTError) as ctx:
            w.apply_command(self.base, mock.TOKEN, issues["OE-3"], "Статус задачи нет-такого")
        self.assertIn("Переход недоступен", str(ctx.exception))

    def test_priority_and_sprint_options(self):
        issues = {i["idReadable"]: i for i in w.fetch_issues(self.base, mock.TOKEN, "q", 50)}
        pr = w.fetch_field_options(self.base, mock.TOKEN, issues["OE-1"], "Priority", "priority")
        self.assertEqual(pr["current"], ["Критический"])
        self.assertIn(("Высокий", "Приоритет Высокий", False), pr["options"])
        self.assertIn(("Критический", "Приоритет Критический", True), pr["options"])
        w.apply_command(self.base, mock.TOKEN, issues["OE-1"], "Приоритет Высокий")
        # спринт: текущий — «убрать», активные — перенос; архивный 26.20 не предлагается
        sp = w.fetch_field_options(self.base, mock.TOKEN, issues["OE-1"], "", "sprint")
        self.assertEqual(sp["current"], ["26.22"])
        self.assertEqual(sp["options"], [("26.22", "remove Спринт 26.22", True),
                                         ("тестовый спринт 24", "remove Спринт 26.22 add Спринт тестовый спринт 24", False)])
        w.apply_command(self.base, mock.TOKEN, issues["OE-1"], sp["options"][1][1])
        new = {i["idReadable"]: i for i in w.fetch_issues(self.base, mock.TOKEN, "q", 50)}
        self.assertEqual(w.field_value(new["OE-1"], "Priority", "priority")[0], "Высокий")
        self.assertEqual([n for n, _ in w.sprints_of(new["OE-1"], "")], ["тестовый спринт 24"])
        # задача без спринта: только «добавить»
        none = w.fetch_field_options(self.base, mock.TOKEN, issues["OE-4"], "", "sprint")
        self.assertEqual(none["options"][0], ("26.22", "add Спринт 26.22", False))

    def test_state_options_no_field(self):
        with self.assertRaises(w.YTError):
            w.fetch_state_options(self.base, mock.TOKEN, issue_with(), "State")

    def test_description(self):
        issues = w.fetch_issues(self.base, mock.TOKEN, "q", 50)
        self.assertIn("обновить", w.fetch_description(self.base, mock.TOKEN, issues[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
