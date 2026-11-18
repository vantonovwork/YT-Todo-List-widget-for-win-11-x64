"""Тесты интерфейса и сценариев: настоящий виджет (Tk) против тестового сервера YouTrack."""
import ctypes
import json
import os
import queue
import time
import unittest

from common import w, pump, wait_for
import mock_youtrack as mock

ctypes.windll.shcore.SetProcessDpiAwareness(1)
w.load_icon_font()
# Тестовые окна не должны отнимать фокус клавиатуры у пользователя, который в это время работает:
# иначе его нажатия попадают в поля тестовых окон (и тесты «случайно» падают).
REAL_FOCUS_FORCE = w.tk.Misc.focus_force  # для тестов, которым нужен настоящий фокус (клавиши)
w.tk.Misc.focus_force = w.tk.Misc.focus_set


class Ev:
    def __init__(self, x, y):
        self.x, self.y, self.x_root, self.y_root = x, y, 0, 0


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = mock.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        mock.control(self.base, "/test/reset")
        for p in (w.PLAN_PATH, w.SEEN_PATH, w.URGENT_PATH, w.ATTN_PATH, w.LOG_PATH):
            if os.path.exists(p):
                os.remove(p)
        self.toasts, self.boxes, self.opened = [], [], []
        w.show_toast = lambda *a: self.toasts.append(a)
        for name in ("showerror", "showwarning", "showinfo"):
            setattr(w.messagebox, name, lambda *a, _n=name, **k: self.boxes.append((_n,) + a))
        w.messagebox.askyesno = lambda *a, **k: True
        w.os.startfile = lambda url: self.opened.append(url)
        self.errors = []

    def make_app(self, **cfg):
        base = dict(w.DEFAULTS, base_url=self.base, token_enc=w.protect(mock.TOKEN), geometry="440x700+60+60",
                    hotkey="", tray=False, notify=True, tray_hint_shown=True)
        base["geometry"] = "700x700+60+60"  # широкое окно — полные названия вкладок
        base.update(cfg)
        w.save_cfg(base)
        app = w.Widget(queue.Queue())
        app.report_callback_exception = lambda *a: self.errors.append(a)
        self.addCleanup(self.close_app, app)
        wait_for(app, lambda: app.issues or app.cards, what="загрузка задач")
        pump(app, 0.2)
        return app

    def close_app(self, app):
        try:
            if app.tray:
                app.tray.stop()
            w.stop_hotkey(app._hotkey_tid)
            app.destroy()
        except Exception:
            pass
        self.assertEqual(self.errors, [], "ошибки в обработчиках Tk")

    # помощники
    def ids(self, app):
        return [c["id"] for c in app.visible]

    def card(self, app, iid):
        return next(c for c in app.cards if c["id"] == iid)

    def click(self, app, tag):
        cv = app.canvas
        x1, y1, x2, y2 = cv.bbox(tag)
        ev = Ev((x1 + x2) / 2 - cv.canvasx(0), (y1 + y2) / 2 - cv.canvasy(0))
        app._on_press(ev)
        app._on_release(ev)
        pump(app, 0.05)

    def texts(self, app, tag="all"):
        cv = app.canvas
        return [cv.itemcget(i, "text") for i in cv.find_withtag(tag) if cv.type(i) == "text"]


class TestListAndFilters(Base):
    def test_initial_render(self):
        app = self.make_app()
        self.assertEqual(len(app.cards), 6)
        self.assertEqual(self.ids(app)[:2], ["OE-1", "OE-2"])
        texts = self.texts(app)
        for t in ("Открыта", "Критический", "сегодня", "завтра", "26.22", "26.22 +1", "Срок", "Спринт"):
            self.assertIn(t, texts)
        self.assertIn("Все мои задачи  6", app.tab_widgets["all"][0].cget("text"))
        self.assertTrue(app.status_lbl.cget("text").startswith("Обновлено"))
        cw = app.canvas.winfo_width()
        self.assertFalse([i for i in app.canvas.find_all() if app.canvas.bbox(i)[2] > cw], "ничего не вылезает за край")

    def test_search_debounce_and_escape(self):
        app = self.make_app()
        app.search.event_generate("<FocusIn>")
        calls = []
        orig = app._apply_filter
        app._apply_filter = lambda *a, **k: (calls.append(1), orig(*a, **k))
        for t in ("с", "ср", "срок"):
            app.search_var.set(t)
            pump(app, 0.05)
        self.assertEqual(calls, [], "фильтр не срабатывает на каждый символ")
        pump(app, 0.5)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.ids(app), ["OE-2", "OE-3", "OE-4", "OE-5"])  # «Без срока…» тоже содержит «срок»
        app.search_var.set("нет такого")
        app._apply_filter_now()
        self.assertIn("Ничего не найдено", self.texts(app))
        REAL_FOCUS_FORCE(app.search)  # Esc приходит только в поле с настоящим фокусом
        pump(app, 0.1)
        app.search.event_generate("<Escape>")
        pump(app, 0.1)
        self.assertEqual(len(app.visible), 6)

    def test_filters_combined_and_persisted(self):
        app = self.make_app()
        app.prio_vars["Критический"].set(True)
        app.prio_vars["Высокий"].set(True)
        app._on_prio_change()
        self.assertEqual(self.ids(app), ["OE-1", "OE-2"])
        self.assertEqual(app.prio_text.cget("text"), "Приоритет: 2")
        app.due_hot_var.set(True)
        app.sprint_vars["26.20"].set(True)
        app._on_prio_change()
        self.assertEqual(self.ids(app), ["OE-2"])
        self.assertEqual(w.load_cfg()["prio_filter"], ["Критический", "Высокий"])
        self.assertTrue(w.load_cfg()["due_hot"])
        app._reset_prio()
        self.assertEqual(len(app.visible), 6)
        app.prio_vars[app.NO_PRIO].set(True)
        app._on_prio_change()
        self.assertEqual(self.ids(app), ["OE-5"])
        app._reset_prio()
        app.sprint_vars[app.NO_SPRINT].set(True)
        app._on_prio_change()
        self.assertEqual(self.ids(app), ["OE-4", "OE-5", "OE-7"])
        app._reset_prio()

    def test_filter_menu_contents(self):
        app = self.make_app()
        labels = [app.prio_menu.entrycget(i, "label") for i in range(app.prio_menu.index("end") + 1)
                  if app.prio_menu.type(i) != "separator"]
        self.assertEqual(labels[:2], ["Горящие сроки (просрочено, сегодня, завтра)", "Приоритет:"])
        self.assertIn("(без приоритета)", labels)
        self.assertIn("26.22  · текущий", labels)
        self.assertEqual(labels[-1], "Сбросить фильтры")

    def test_unchanged_data_not_redrawn(self):
        app = self.make_app()
        renders = []
        orig = app.render
        app.render = lambda *a, **k: (renders.append(1), orig(*a, **k))
        app.refresh()
        wait_for(app, lambda: not app.loading, what="обновление")
        pump(app, 0.2)
        self.assertEqual(renders, [], "без изменений в YouTrack список не перерисовывается")
        mock.control(self.base, "/test/comment")
        app.refresh()
        wait_for(app, lambda: renders, what="перерисовка после изменений")


class TestPlan(Base):
    def test_star_and_day_tab(self):
        app = self.make_app()
        renders = []
        orig = app.render
        app.render = lambda *a, **k: (renders.append(1), orig(*a, **k))
        self.click(app, "s2")  # звёздочка OE-3
        self.click(app, "s0")  # OE-1
        self.assertEqual(app.plan, ["OE-3", "OE-1"])
        self.assertEqual(renders, [], "звёздочка не перерисовывает весь список")
        self.assertEqual(w.load_plan()[0], ["OE-3", "OE-1"])
        app.set_tab("day")
        self.assertEqual(self.ids(app), ["OE-3", "OE-1"], "порядок как добавляли")
        self.assertEqual(w.load_cfg()["tab"], "day")
        self.assertIn("На день  2", app.tab_widgets["day"][0].cget("text"))

    def test_done_drag_clear_and_new_day(self):
        w.save_plan(["OE-1", "OE-2", "OE-3"], set())
        app = self.make_app(tab="day")
        self.assertEqual(self.ids(app), ["OE-1", "OE-2", "OE-3"])
        self.click(app, "k0")  # галочка OE-1 → в конец
        self.assertEqual(self.ids(app), ["OE-2", "OE-3", "OE-1"])
        self.assertEqual(w.load_plan()[1], {"OE-1"})
        self.assertEqual(app.canvas.itemcget(app.canvas.find_withtag("t2")[0], "font"), str(app.f_title_done))
        # перетаскивание OE-3 (индекс 1) на первое место за ручку
        cv = app.canvas
        x1, y1, x2, y2 = cv.bbox("h1")
        x, y = (x1 + x2) / 2, (y1 + y2) / 2
        app._on_press(Ev(x, y))
        for k in range(1, 15):
            app._on_drag(Ev(x, y - k * 12))
            app.update()
        app._on_release(Ev(x, y - 14 * 12))
        self.assertEqual(self.ids(app), ["OE-3", "OE-2", "OE-1"])
        self.assertEqual(w.load_plan()[0][:3], ["OE-3", "OE-2", "OE-1"])
        self.assertEqual(cv.canvasy(0), 0, "короткий список не сдвигается")
        # поиск скрывает часть задач плана — перетаскивать нельзя
        app.search.event_generate("<FocusIn>")
        app.search_var.set("завтра")
        app._apply_filter_now()
        before = list(app.plan)
        x1, y1, x2, y2 = cv.bbox("h0")
        app._on_press(Ev((x1 + x2) / 2, (y1 + y2) / 2))
        app._on_drag(Ev((x1 + x2) / 2, y2 + 80))
        app._on_release(Ev((x1 + x2) / 2, y2 + 80))
        self.assertEqual(app.plan, before)
        self.assertIn("скрыта поиском или фильтром", app.status_lbl.cget("text"))
        app.search_var.set("")
        app._apply_filter_now()
        # новый день — галочки сбрасываются
        app._done_date = "2000-01-01"
        app.render()
        self.assertEqual(app.plan_done, set())
        # очистить план
        app.clear_plan()
        self.assertEqual(app.plan, [])
        self.assertIn("План на день пуст", "".join(self.texts(app)))


class TestQuickViewAndComments(Base):
    def test_quick_view(self):
        w.save_seen({"OE-1": 0})  # у OE-1 один непрочитанный комментарий
        app = self.make_app()
        self.assertEqual(app.count_lbl.cget("text"), "1 нов.")
        self.click(app, "i0")
        self.assertIn("OE-1", app.expanded)
        wait_for(app, lambda: isinstance(app.details.get("OE-1"), dict), what="содержимое задачи")
        pump(app, 0.3)
        body = self.detail_text(app, "OE-1")
        self.assertIn("Нужно обновить выгрузку", body)
        self.assertIn("код", body)
        self.assertIn("Илья Гаврилов", body)
        texts = "\n".join(self.texts(app, "c0"))
        self.assertIn("Открыть в YouTrack", texts)
        self.assertNotIn("Полностью", texts, "отдельной кнопки «Полностью» нет")
        self.assertEqual(app.count_lbl.cget("text"), "", "просмотр помечает комментарии прочитанными")
        self.click(app, "o0")
        self.assertEqual(self.opened[-1], f"{self.base}/issue/OE-1")
        self.click(app, "i0")
        self.assertNotIn("OE-1", app.expanded)

    def test_write_comment(self):
        app = self.make_app()
        # ссылка «Написать комментарий» в быстром просмотре OE-2
        self.click(app, "i1")
        wait_for(app, lambda: isinstance(app.details.get("OE-2"), dict), what="содержимое задачи")
        pump(app, 0.1)
        self.assertIn("Написать комментарий", "\n".join(self.texts(app, "c1")))
        self.click(app, "m1")
        win = app._comment_win
        self.assertTrue(win.winfo_exists())
        self.assertEqual(win.title(), "Комментарий к OE-2")
        # пустой текст не отправляется
        win._send()
        self.assertIn("Введите текст", win._err.cget("text"))
        # черновик: закрыли с текстом — при повторном открытии текст на месте
        win._text.insert("1.0", "Черновик ответа")
        win._close()
        self.assertFalse(win.winfo_exists())
        app.open_comment_editor(self.card(app, "OE-2")["issue"])
        win = app._comment_win
        self.assertEqual(win._text.get("1.0", "end-1c"), "Черновик ответа")
        # ошибка сервера: окно остаётся, текст не теряется
        mock.control(self.base, "/test/mode?m=500")
        win._send()
        wait_for(app, lambda: "Не отправлено" in win._err.cget("text"), what="сообщение об ошибке")
        self.assertTrue(win.winfo_exists())
        self.assertEqual(win._text.get("1.0", "end-1c"), "Черновик ответа")
        # успешная отправка
        mock.control(self.base, "/test/mode?m=ok")
        win._text.delete("1.0", "end")
        win._text.insert("1.0", "Проверил, всё ок")
        win._send()
        wait_for(app, lambda: not win.winfo_exists(), what="окно закрылось после отправки")
        self.assertEqual(mock.State.commands[-1], {"comment": "Проверил, всё ок", "issue": "OE-2"})
        self.assertIn("комментарий добавлен", app.status_lbl.cget("text"))
        wait_for(app, lambda: "Проверил, всё ок" in self.detail_text(app, "OE-2"),
                 what="новый комментарий виден в раскрытой карточке")
        self.assertEqual(app.new_comments(self.card(app, "OE-2")["issue"]), 0, "свой комментарий не «новый»")
        # после обновления списка — без уведомления о своём же комментарии
        app.refresh()
        wait_for(app, lambda: not app.loading, what="обновление")
        pump(app, 0.2)
        self.assertEqual(self.toasts, [])
        # пункт контекстного меню
        labels = [app.menu.entrycget(i, "label") for i in range(app.menu.index("end") + 1)
                  if app.menu.type(i) not in ("separator",)]
        self.assertIn("Написать комментарий…", labels)

    def detail_text(self, app, iid):
        cache = app._detail_cache.get(iid)
        return cache["widget"].get("1.0", "end-1c") if cache and cache["widget"].winfo_exists() else ""

    def test_title_click_opens_and_marks_read(self):
        w.save_seen({"OE-1": 0})
        app = self.make_app()
        app.open_issue(self.card(app, "OE-1")["issue"])  # «Открыть в YouTrack»
        self.assertEqual(self.opened, [f"{self.base}/issue/OE-1"])
        self.assertEqual(app.new_comments(self.card(app, "OE-1")["issue"]), 0)
        self.assertGreater(w.load_seen()["OE-1"], 0)


class TestStateChange(Base):
    def test_change_ok_and_error(self):
        app = self.make_app()
        issue = self.card(app, "OE-1")["issue"]
        app.menu_issue = issue
        app._prefetch_states(issue)
        wait_for(app, lambda: isinstance(app._state_cache.get("OE-1"), list), what="переходы")
        app._fill_state_menu()
        labels = [app.state_menu.entrycget(i, "label") for i in range(app.state_menu.index("end") + 1)]
        self.assertEqual(labels, ["Ждет БА", "Проработка БА", "Отклонена"])
        app.state_menu.invoke(0)
        wait_for(app, lambda: self.card(app, "OE-1")["state"] == "Ждет БА", what="новый статус")
        self.assertTrue(json.loads(json.dumps(mock.State.commands))[-1]["query"].endswith("-->"))
        app.change_state(self.card(app, "OE-1")["issue"], "Отклонена", "Статус задачи отклонена")
        wait_for(app, lambda: self.boxes, what="сообщение об ошибке")
        self.assertEqual(self.boxes[-1][0], "showerror")
        self.assertIn("статус не изменён", app.status_lbl.cget("text"))

    def test_no_transitions(self):
        app = self.make_app()
        mock.State.issues[0]["customFields"][0]["value"]["name"] = "Нет переходов"
        app.menu_issue = self.card(app, "OE-1")["issue"]
        app._fill_state_menu()
        self.assertEqual(app.state_menu.entrycget(0, "label"), "Нет доступных переходов")


class TestFieldMenus(Base):
    """Левый клик по плашке статуса / приоритета / спринта открывает список значений из YouTrack."""

    def setUp(self):
        super().setUp()
        self._popup = w.tk.Menu.tk_popup
        w.tk.Menu.tk_popup = lambda *a, **k: None  # меню не показываем (оно модальное), а проверяем его пункты
        self.addCleanup(setattr, w.tk.Menu, "tk_popup", self._popup)

    def labels(self, app):
        m = app.field_menu
        return [m.entrycget(i, "label") for i in range(m.index("end") + 1)]

    def pick(self, app, text):
        m = app.field_menu
        idx = next(i for i in range(m.index("end") + 1) if m.entrycget(i, "label").strip().startswith(text))
        m.invoke(idx)

    def test_status_priority_sprint(self):
        app = self.make_app()
        # статус OE-1 (Открыта): только разрешённые переходы
        self.click(app, "a0")
        self.assertEqual([l.strip() for l in self.labels(app)],
                         ["Сейчас: Открыта — перевести в:", "Ждет БА", "Проработка БА", "Отклонена"])
        self.pick(app, "Ждет БА")
        wait_for(app, lambda: self.card(app, "OE-1")["state"] == "Ждет БА", what="новый статус")
        self.assertIn("статус → Ждет БА", app.status_lbl.cget("text"))
        # приоритет OE-1: все значения, текущий отмечен и недоступен
        self.click(app, "p0")
        labels = self.labels(app)
        self.assertIn("✓ Критический", labels)
        self.assertEqual(app.field_menu.entrycget(labels.index("✓ Критический"), "state"), "disabled")
        self.pick(app, "Высокий")
        wait_for(app, lambda: self.card(app, "OE-1")["prio"] == "Высокий", what="новый приоритет")
        # спринт у задачи без спринта (OE-4): на карточке «выбрать ▾», в списке — активные спринты
        idx = self.ids(app).index("OE-4")
        self.assertIn("выбрать", self.texts(app, f"r{idx}"))
        self.click(app, f"r{idx}")
        self.assertEqual([l.strip() for l in self.labels(app)],
                         ["Спринт (не задан):", "26.22", "тестовый спринт 24"], "архивный 26.20 не предлагается")
        self.pick(app, "26.22")
        wait_for(app, lambda: self.card(app, "OE-4")["sprints"] == [("26.22", True)], what="спринт назначен")
        # перенос в другой спринт и удаление из спринта
        idx = self.ids(app).index("OE-4")
        self.click(app, f"r{idx}")
        self.pick(app, "тестовый спринт 24")
        wait_for(app, lambda: [n for n, _ in self.card(app, "OE-4")["sprints"]] == ["тестовый спринт 24"],
                 what="перенос в другой спринт")
        idx = self.ids(app).index("OE-4")
        self.click(app, f"r{idx}")
        self.pick(app, "✓ тестовый спринт 24")
        wait_for(app, lambda: self.card(app, "OE-4")["sprints"] == [], what="убрано из спринта")
        self.assertEqual([c["query"] for c in mock.State.commands][-3:],
                         ["add Спринт 26.22", "remove Спринт 26.22 add Спринт тестовый спринт 24",
                          "remove Спринт тестовый спринт 24"])

    def test_error_shown_in_menu(self):
        app = self.make_app()
        mock.control(self.base, "/test/mode?m=500")
        self.click(app, "p0")
        self.assertIn("HTTP 500", self.labels(app)[0])


class TestNotifications(Base):
    def refresh(self, app):
        app.refresh()
        wait_for(app, lambda: not app.loading, what="обновление")
        pump(app, 0.1)

    def test_events(self):
        app = self.make_app()
        self.assertEqual(self.toasts, [], "при запуске уведомлений нет")
        mock.control(self.base, "/test/my-comment")
        self.refresh(app)
        self.assertEqual(self.toasts, [], "свой комментарий — без уведомления")
        mock.control(self.base, "/test/comment")
        mock.control(self.base, "/test/new-issue")
        self.refresh(app)
        titles = [t[0] for t in self.toasts]
        self.assertEqual(titles, ["Комментарий · OE-2", "Назначена задача · OE-9"])
        self.assertEqual(self.toasts[0][1], "Илья Гаврилов: Нужна проверка на тесте, см. лог")
        self.assertEqual(self.toasts[0][2], f"{self.base}/issue/OE-2")
        self.refresh(app)
        self.assertEqual(len(self.toasts), 2, "повторно об одном и том же не уведомляем")
        mock.control(self.base, "/test/many-comments")
        self.refresh(app)
        self.assertEqual(len(self.toasts), 3)
        self.assertEqual(self.toasts[-1][0], "YouTrack: комментариев: 5", "много событий — одно сводное уведомление")

    def test_disabled(self):
        app = self.make_app(notify=False)
        mock.control(self.base, "/test/comment")
        self.refresh(app)
        self.assertEqual(self.toasts, [])


class TestErrorsAndRecovery(Base):
    def test_server_errors(self):
        mock.control(self.base, "/test/mode?m=401")
        w.save_cfg(dict(w.DEFAULTS, base_url=self.base, token_enc=w.protect(mock.TOKEN), tray=False, hotkey=""))
        app = w.Widget(queue.Queue())
        self.addCleanup(self.close_app, app)
        wait_for(app, lambda: "Доступ запрещён" in app.status_lbl.cget("text"), what="ошибка 401")
        self.assertIn("Доступ запрещён", "".join(self.texts(app)))
        mock.control(self.base, "/test/mode?m=ok")
        app.refresh()
        wait_for(app, lambda: len(app.cards) == 6, what="восстановление")
        mock.control(self.base, "/test/mode?m=500")
        app.refresh()
        wait_for(app, lambda: "HTTP 500" in app.status_lbl.cget("text"), what="ошибка 500")
        self.assertEqual(len(app.cards), 6, "при ошибке старые данные остаются на экране")
        self.assertIn("данные от", app.status_lbl.cget("text"))

    def test_not_configured(self):
        w.save_cfg(dict(w.DEFAULTS, tray=False, hotkey=""))
        app = w.Widget(queue.Queue())
        self.addCleanup(self.close_app, app)
        wait_for(app, lambda: getattr(app, "_settings", None) is not None, what="окно настроек")
        self.assertIn("Нажмите ⚙", "".join(self.texts(app)))


class TestAppearanceAndWindow(Base):
    def find(self, root, cls, pred=lambda w_: True):
        out = []
        for ch in root.winfo_children():
            if isinstance(ch, cls) and pred(ch):
                out.append(ch)
            out += self.find(ch, cls, pred)
        return out

    def test_settings_save_cancel_and_hotkey_validation(self):
        app = self.make_app()
        app.open_settings()
        win = app._settings
        pump(app, 0.2)
        e_hk = self.find(win, w.tk.Entry, lambda e: e.get() == "")[-1]
        e_hk.insert(0, "Hyper+Q")
        save = self.find(win, w.tk.Button, lambda b: b.cget("text") == "Сохранить")[0]
        save.invoke()
        self.assertEqual(self.boxes[-1][0], "showwarning", "неверное сочетание отклонено")
        self.assertTrue(win.winfo_exists())
        e_hk.delete(0, "end")
        save.invoke()
        pump(app, 0.3)
        self.assertFalse(win.winfo_exists())
        wait_for(app, lambda: len(app.cards) == 6, what="перезагрузка после сохранения")
        # предпросмотр темы и отмена
        app.open_settings()
        win = app._settings
        pump(app, 0.2)
        light = [b for b in self.find(win, w.tk.Label, lambda l: l.cget("text") == "Светлая")][0]
        light.event_generate("<Button-1>")
        pump(app, 0.2)
        self.assertEqual(w.CARD, "#ffffff")
        cancel = self.find(win, w.tk.Button, lambda b: b.cget("text") == "Отмена")[0]
        cancel.invoke()
        pump(app, 0.2)
        self.assertEqual(w.CARD, w.load_cfg()["card_color"], "отмена возвращает тему")
        self.assertEqual(len(app.cards), 6)

    def test_theme_font_rebuild(self):
        app = self.make_app()
        for bg, card, fs in (("#0f2742", "#173a5e", 16), ("#eef0f3", "#ffffff", 9), ("#1f1f1f", "#2b2b2b", 12)):
            w.apply_theme(bg, card, fs)
            app.rebuild()
            pump(app, 0.1)
            self.assertEqual(len(app.visible), 6)
            self.assertEqual(app.f_title.cget("size"), fs)

    def test_collapse_topmost_visibility(self):
        app = self.make_app()
        h = app.winfo_height()
        app.toggle_collapse()
        pump(app, 0.1)
        self.assertLess(app.winfo_height(), 60)
        app.toggle_collapse()
        pump(app, 0.1)
        self.assertEqual(app.winfo_height(), h)
        app.toggle_topmost()
        self.assertTrue(w.load_cfg()["topmost"])
        app.toggle_topmost()
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(app.winfo_id())
        orig = user32.GetForegroundWindow
        try:
            user32.GetForegroundWindow = lambda: hwnd  # виджет активен → горячая клавиша прячет
            app.toggle_visible()
            pump(app, 0.1)
            self.assertEqual(app.state(), "withdrawn")
            user32.GetForegroundWindow = lambda: 0      # спрятан/не активен → показывает
            app.toggle_visible()
            pump(app, 0.1)
            self.assertEqual(app.state(), "normal")
        finally:
            user32.GetForegroundWindow = orig

    def test_resize_performance_and_layout(self):
        mock.control(self.base, "/test/bulk?n=60")
        app = self.make_app()
        self.assertEqual(len(app.cards), 60)
        steps = list(range(440, 340, -6)) + list(range(340, 620, 14))
        t0 = time.perf_counter()
        for wd in steps:
            app.geometry(f"{wd}x700")
            app.update()
        per_step = (time.perf_counter() - t0) * 1000 / len(steps)
        pump(app, 0.4)
        print(f"\n    ресайз: {per_step:.0f} мс/шаг ", end="")
        self.assertLess(per_step, 80)
        self.assertEqual(len(app.card_items), 60, "после остановки нарисованы все карточки")
        cw = app.canvas.winfo_width()
        self.assertFalse([i for i in app.canvas.find_all() if app.canvas.bbox(i)[2] > cw])
        app.canvas.yview_scroll(2000, "units")
        top = app.canvas.canvasy(0)
        app.geometry("500x700")
        pump(app, 0.4)
        self.assertAlmostEqual(app.canvas.canvasy(0), top, delta=1, msg="прокрутка сохраняется при ресайзе")


class TestSystem(Base):
    def test_tray(self):
        w.save_seen({"OE-1": 0})
        app = self.make_app(tray=True)
        wait_for(app, lambda: app.tray and app.tray.hwnd, what="значок в трее")
        self.assertTrue(app.tray.added)
        self.assertEqual(app.tray.count, 1)
        self.assertIn("новых комментариев: 1", app.tray.tip)
        app.on_close()
        pump(app, 0.1)
        self.assertEqual(app.state(), "withdrawn", "✕ прячет в трей")
        app.q.put(("tray", 1))
        wait_for(app, lambda: app.state() == "normal", what="показ из трея")
        app.q.put(("tray", 3))
        wait_for(app, lambda: app.tray.count == 0, what="«отметить всё прочитанным»")
        app.cfg["tray"] = False
        app.setup_tray()
        self.assertIsNone(app.tray)

    def test_close_without_tray_quits(self):
        app = self.make_app(tray=False)
        app.on_close()
        with self.assertRaises(w.tk.TclError):
            app.winfo_exists() and app.state()

    def test_hotkey_register_conflict_toggle(self):
        app = self.make_app(hotkey="Ctrl+Alt+Shift+F11")
        wait_for(app, lambda: app._hotkey_tid, what="регистрация")
        tid2, ok2 = w.start_hotkey("Ctrl+Alt+Shift+F11", queue.Queue())
        self.assertFalse(ok2, "второй раз то же сочетание не регистрируется")
        self.assertIsNone(tid2)
        user32 = ctypes.windll.user32

        def signal():  # то же сообщение WM_HOTKEY, что Windows шлёт при нажатии
            user32.PostThreadMessageW(app._hotkey_tid, 0x0312, 1, 0)

        app.withdraw()
        pump(app, 0.1)
        signal()  # спрятан → показать
        wait_for(app, lambda: app.state() == "normal", what="горячая клавиша показала окно")
        hwnd = user32.GetParent(app.winfo_id())
        orig = user32.GetForegroundWindow
        try:
            user32.GetForegroundWindow = lambda: hwnd
            signal()  # активен → спрятать
            wait_for(app, lambda: app.state() == "withdrawn", what="горячая клавиша спрятала окно")
        finally:
            user32.GetForegroundWindow = orig
        app.cfg["hotkey"] = ""
        app.setup_hotkey()
        self.assertIsNone(app._hotkey_tid, "пустое поле — горячая клавиша выключена")

    def test_hotkey_real_keyboard(self):
        """Настоящее нажатие через эмуляцию клавиатуры (Windows может блокировать её в некоторых сессиях)."""
        q = queue.Queue()
        tid, ok = w.start_hotkey("Ctrl+Alt+Shift+F11", q)
        self.addCleanup(w.stop_hotkey, tid)
        self.assertTrue(ok)
        u = ctypes.windll.user32
        for vk in (0x11, 0x12, 0x10, 0x7A):
            u.keybd_event(vk, 0, 0, 0)
        for vk in (0x7A, 0x10, 0x12, 0x11):
            u.keybd_event(vk, 0, 2, 0)
        time.sleep(0.6)
        if q.empty():
            self.skipTest("Windows сейчас блокирует эмуляцию клавиатуры (активно окно с другими правами)")
        self.assertEqual(q.get(), ("hotkey", None))

    def test_single_instance(self):
        w.INSTANCE_PORT = 47690
        q = queue.Queue()
        self.assertTrue(w.acquire_single_instance(q))
        self.assertFalse(w.acquire_single_instance(queue.Queue()), "второй экземпляр не запускается")
        self.assertEqual(q.get(timeout=3), ("show", None), "а просит первый показаться")

    def test_autostart(self):
        os.makedirs(os.path.dirname(w.STARTUP_LNK), exist_ok=True)
        w.set_autostart(True)
        self.assertTrue(os.path.exists(w.STARTUP_LNK))
        w.set_autostart(False)
        self.assertFalse(os.path.exists(w.STARTUP_LNK))

    def test_real_toast_powershell(self):
        import subprocess
        from xml.sax.saxutils import escape
        xml = (f"<toast><visual><binding template=\"ToastGeneric\"><text>{escape('YouTrack Widget · автотест')}</text>"
               f"<text>{escape('Проверка уведомлений. Можно закрыть.')}</text></binding></visual></toast>")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", w._TOAST_PS],
                           env=dict(os.environ, YTW_XML=xml, YTW_AUMID=w.AUMID), capture_output=True,
                           creationflags=0x08000000, timeout=60)
        self.assertEqual((r.returncode, r.stderr.strip()), (0, b""))


class TestUrgent(Base):
    def editor(self, app, item=None):
        app.open_urgent_editor(item)
        pump(app, 0.2)
        return app._urgent_win

    def test_create_limits_and_validation(self):
        app = self.make_app()
        app.set_tab("urgent")
        self.assertFalse(app.prio_btn.winfo_ismapped(), "фильтр приоритетов на «Срочных» скрыт")
        self.assertIn("Срочных задач нет", "".join(self.texts(app)))
        win = self.editor(app)
        win._save()
        self.assertTrue(win.winfo_exists(), "без названия не сохраняется")
        win._title_var.set("Н" * 150)
        pump(app, 0.05)
        self.assertEqual(len(win._title_var.get()), 100, "название не длиннее 100 символов")
        win._text.insert("1.0", "т" * 1500)
        pump(app, 0.05)
        self.assertEqual(len(win._text.get("1.0", "end-1c")), 1000, "текст не длиннее 1000 символов")
        win._title_var.set("Написать Ивану — уточнить сроки")
        win._text.delete("1.0", "end")
        win._text.insert("1.0", "Про выгрузку цен Expedia")
        win._save()
        pump(app, 0.1)
        self.assertFalse(win.winfo_exists())
        self.assertEqual(len(app.urgent), 1)
        self.assertEqual(app.visible[0]["summary"], "Написать Ивану — уточнить сроки")
        self.assertIn("Про выгрузку цен Expedia", self.texts(app))
        self.assertIn("Срочные  1", app.tab_widgets["urgent"][0].cget("text"))
        with open(w.URGENT_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved[0]["title"], "Написать Ивану — уточнить сроки")
        self.assertFalse(any("/api/commands" in r or r.startswith("/api/issues/") and "POST" in r
                             for r in mock.State.requests))
        self.assertEqual(mock.State.commands, [], "в YouTrack ничего не отправлено")

    def test_done_edit_delete_search(self):
        app = self.make_app(tab="urgent")
        a = app.add_urgent("Первая", "")
        time.sleep(0.01)
        b = app.add_urgent("Вторая", "текст второй")
        self.assertEqual(self.ids(app), [b["id"], a["id"]], "новые сверху")
        self.click(app, "k0")  # «Вторая» выполнена → вниз
        self.assertEqual(self.ids(app), [a["id"], b["id"]])
        self.assertTrue(app.urgent[1]["done"])
        self.assertIn("Срочные  1", app.tab_widgets["urgent"][0].cget("text"))
        pump(app, 0.1)
        self.assertTrue(app.sweep_btn.winfo_ismapped(), "кнопка «удалить выполненные» появилась")
        # изменить через клик по названию
        self.click(app, "t0")
        win = app._urgent_win
        win._title_var.set("Первая (исправлено)")
        win._save()
        pump(app, 0.1)
        self.assertEqual(app.visible[0]["summary"], "Первая (исправлено)")
        # поиск по тексту
        app.search.event_generate("<FocusIn>")
        app.search_var.set("второй")
        app._apply_filter_now()
        self.assertEqual(self.ids(app), [b["id"]])
        app.search_var.set("")
        app._apply_filter_now()
        # удалить выполненные, затем удалить одну
        app.clear_done_urgent()
        self.assertEqual([u["title"] for u in app.urgent], ["Первая (исправлено)"])
        self.click(app, "x0")
        self.assertEqual(app.urgent, [])
        self.assertEqual(w.load_urgent(), [])
        app.set_tab("all")
        pump(app, 0.1)
        self.assertTrue(app.prio_btn.winfo_ismapped(), "на других вкладках фильтр вернулся")
        self.assertFalse(app.urgent_btns.winfo_ismapped())
        # узкое окно — короткие названия вкладок, всё помещается
        app.geometry("340x700")
        pump(app, 0.4)
        self.assertEqual(app.tab_widgets["all"][0].cget("text").split()[0], "Все")
        tabs_row = app.tab_widgets["all"][0].master.master.master
        self.assertLessEqual(tabs_row.winfo_reqwidth(), tabs_row.winfo_width() + 1, "ряд вкладок помещается")

    def test_works_without_youtrack(self):
        w.save_urgent([{"id": "U-1", "title": "Позвонить", "text": "", "created": 1, "done": False}])
        mock.control(self.base, "/test/mode?m=500")
        w.save_cfg(dict(w.DEFAULTS, base_url=self.base, token_enc=w.protect(mock.TOKEN), tray=False, hotkey="",
                        tab="urgent"))
        app = w.Widget(queue.Queue())
        self.addCleanup(self.close_app, app)
        wait_for(app, lambda: "HTTP 500" in app.status_lbl.cget("text"), what="ошибка сервера")
        pump(app, 0.3)
        self.assertEqual(self.ids(app), ["U-1"], "срочные задачи видны, даже когда YouTrack недоступен")


class TestAttention(Base):
    """Вкладка «Нужно внимание»: задачи, где вас упомянули (в том числе чужие)."""

    def test_tab_cards_and_toggle(self):
        app = self.make_app()
        wait_for(app, lambda: len(app.mentions) == 3, what="загрузка упоминаний")
        self.assertIn("Нужно внимание  2", app.tab_widgets["attention"][0].cget("text"),
                      "ждут ответа OE-50 и OE-1 (в OE-51 вы уже ответили)")
        app.set_tab("attention")
        pump(app, 0.1)
        self.assertEqual(self.ids(app), ["OE-50", "OE-1", "OE-51"], "ждущие ответа сверху, новые упоминания выше")
        texts = "\n".join(self.texts(app))
        for t in ("@ Лазарев Алексей", "посмотри, пожалуйста, логи выгрузки", "ждёт ответа", "отвечено", "Ответ"):
            self.assertIn(t, texts)
        self.assertIn("Статус", texts, "у чужих задач тоже есть статус/приоритет с выпадающими списками")
        # ручная отметка «отвечено» — клик по плашке
        self.click(app, "w0")
        self.assertEqual(self.ids(app), ["OE-1", "OE-50", "OE-51"])
        self.assertIn("Нужно внимание  1", app.tab_widgets["attention"][0].cget("text"))
        self.assertIn("OE-50", w.load_attn())
        # новое упоминание после ручной отметки — снова «ждёт ответа» + уведомление
        mock.control(self.base, "/test/mention")
        app.refresh()
        wait_for(app, lambda: not app.loading, what="обновление")
        pump(app, 0.2)
        self.assertEqual(self.ids(app)[0], "OE-50")
        self.assertIn(("Вас упомянули · OE-50", "Павел Драничников: @me ещё вопрос по задаче"),
                      [t[:2] for t in self.toasts])
        # вернуть отметку обратно кликом по «отвечено» у OE-51 → снова ждёт? нет: там вы ответили комментарием
        idx = self.ids(app).index("OE-51")
        self.click(app, f"w{idx}")
        self.assertIn("отвечено", self.texts(app, f"c{self.ids(app).index('OE-51')}"))

    def test_plan_with_foreign_issue_and_search(self):
        app = self.make_app(tab="attention")
        wait_for(app, lambda: app.visible, what="карточки упоминаний")
        self.click(app, "s0")  # звёздочка чужой задачи OE-50
        self.assertIn("OE-50", app.plan)
        app.set_tab("day")
        self.assertEqual(self.ids(app), ["OE-50"], "чужая задача из упоминаний попадает в план на день")
        app.set_tab("attention")
        app.search.event_generate("<FocusIn>")
        app.search_var.set("алина")
        app._apply_filter_now()
        self.assertEqual(self.ids(app), ["OE-51"], "поиск по автору упоминания")

    def test_mentions_error_keeps_main_list(self):
        mock.control(self.base, "/test/mode?m=mentions500")
        app = self.make_app()
        self.assertEqual(len(app.cards), 6, "ошибка загрузки упоминаний не ломает основной список")
        self.assertEqual(app.mentions, [])
        app.set_tab("attention")
        self.assertIn("Вас нигде не упоминали", "".join(self.texts(app)))

    def test_narrow_tabs_fit(self):
        app = self.make_app()
        for wd, label in ((700, "Нужно внимание"), (520, "Внимание"), (340, "")):
            app.geometry(f"{wd}x700")
            pump(app, 0.4)
            text = app.tab_widgets["attention"][0].cget("text")
            self.assertTrue(text.startswith(label) if label else not any(ch.isalpha() for ch in text), (wd, text))
            row = app.tab_widgets["all"][0].master.master.master
            self.assertLessEqual(row.winfo_reqwidth(), row.winfo_width() + 1, f"вкладки помещаются при {wd}")


class KeyEv:
    """Нажатие так, как его присылает Windows в русской раскладке: keysym кириллический, keycode — клавиши V/C/…"""
    def __init__(self, widget, keycode, keysym, state=0x4):
        self.widget, self.keycode, self.keysym, self.state = widget, keycode, keysym, state
        self.x_root = self.y_root = 0


class TestClipboard(Base):
    """Вставка из буфера обмена в окнах комментария и срочной задачи (Ctrl+V в любой раскладке, правый клик)."""

    def setUp(self):
        super().setUp()
        self._popup = w.tk.Menu.tk_popup
        w.tk.Menu.tk_popup = lambda *a, **k: None  # меню модальное — проверяем его пункты, не показывая
        self.addCleanup(setattr, w.tk.Menu, "tk_popup", self._popup)

    def set_clipboard(self, app, text):
        if not hasattr(self, "_saved_clip"):  # вернуть пользователю его буфер после теста
            try:
                self._saved_clip = app.clipboard_get()
            except w.tk.TclError:
                self._saved_clip = None
            self.addCleanup(self.restore_clipboard, app)
        app.clipboard_clear()
        app.clipboard_append(text)
        app.update()

    def restore_clipboard(self, app):
        try:
            if self._saved_clip is not None:
                app.clipboard_clear()
                app.clipboard_append(self._saved_clip)
                app.update()
        except w.tk.TclError:
            pass

    def entry_of(self, win):
        return next(ch for ch in win.winfo_children() if isinstance(ch, w.tk.Entry))

    def test_comment_ctrl_v_russian_layout_and_menu(self):
        app = self.make_app()
        app.open_comment_editor(self.card(app, "OE-1")["issue"])
        pump(app, 0.2)
        text = next(ch for ch in app._comment_win.winfo_children() if isinstance(ch, w.tk.Text))
        self.set_clipboard(app, "Проверил логи.\nВсё ок, @alina посмотри")
        text.focus_set()
        self.assertEqual(w._edit_hotkey(KeyEv(text, 86, "Cyrillic_em")), "break")  # Ctrl+V в русской раскладке
        pump(app, 0.05)
        self.assertEqual(text.get("1.0", "end-1c"), "Проверил логи.\nВсё ок, @alina посмотри")
        # правый клик → «Вставить» (вставка в позицию курсора)
        text.mark_set("insert", "1.0")
        w._edit_menu(KeyEv(text, 0, ""))
        m = text._edit_menu
        labels = [m.entrycget(i, "label") for i in range(m.index("end") + 1) if m.type(i) != "separator"]
        self.assertEqual(labels, ["Вырезать", "Копировать", "Вставить", "Выделить всё"])
        m.invoke(labels.index("Вставить"))
        self.assertTrue(text.get("1.0", "end-1c").startswith("Проверил логи.\nВсё ок, @alina посмотриПроверил"))
        # Ctrl+A (ф) и Ctrl+C (с) тоже по физической клавише
        w._edit_hotkey(KeyEv(text, 65, "Cyrillic_ef"))
        self.assertTrue(text.tag_ranges("sel"))
        w._edit_hotkey(KeyEv(text, 67, "Cyrillic_es"))
        self.assertEqual(app.clipboard_get(), text.get("1.0", "end-1c"))
        # Ctrl+Enter не перехватывается — отправка комментария работает как раньше
        self.assertIsNone(w._edit_hotkey(KeyEv(text, 13, "Return")))
        # без Ctrl (просто клавиша «м») — обычный ввод, не вставка
        self.assertIsNone(w._edit_hotkey(KeyEv(text, 86, "Cyrillic_em", state=0)))
        app._comment_win._close() if hasattr(app._comment_win, "_close") else app._comment_win.destroy()

    def test_urgent_paste_limits(self):
        app = self.make_app()
        app.open_urgent_editor()
        win = app._urgent_win
        pump(app, 0.2)
        entry = self.entry_of(win)
        # многострочный и длинный текст в название: в одну строку и не длиннее 100 символов
        self.set_clipboard(app, "Написать Ивану\nуточнить сроки " + "х" * 200)
        entry.focus_set()
        w._edit_hotkey(KeyEv(entry, 86, "Cyrillic_em"))
        pump(app, 0.05)
        title = win._title_var.get()
        self.assertNotIn("\n", title)
        self.assertTrue(title.startswith("Написать Ивану уточнить сроки"))
        self.assertEqual(len(title), 100)
        # текст: многострочный сохраняется, длина — не больше 1000
        self.set_clipboard(app, "строка 1\nстрока 2\n" + "т" * 1500)
        win._text.focus_set()
        w._edit_hotkey(KeyEv(win._text, 86, "Cyrillic_em"))
        pump(app, 0.05)
        body = win._text.get("1.0", "end-1c")
        self.assertTrue(body.startswith("строка 1\nстрока 2\n"))
        self.assertEqual(len(body), 1000)
        # правый клик в названии → «Вставить» доступна; при пустом поле «Копировать» недоступна
        entry.delete(0, "end")
        w._edit_menu(KeyEv(entry, 0, ""))
        m = entry._edit_menu
        states = {m.entrycget(i, "label"): m.entrycget(i, "state") for i in range(m.index("end") + 1)
                  if m.type(i) != "separator"}
        self.assertEqual((states["Вставить"], states["Копировать"]), ("normal", "disabled"))
        labels = [m.entrycget(i, "label") if m.type(i) != "separator" else "" for i in range(m.index("end") + 1)]
        m.invoke(labels.index("Вставить"))  # вставка через меню: тоже одна строка и не длиннее 100
        pump(app, 0.05)
        self.assertTrue(win._title_var.get().startswith("строка 1 строка 2 т"))
        self.assertEqual(len(win._title_var.get()), 100)
        win._save()
        pump(app, 0.1)
        self.assertEqual(len(app.urgent), 1, "задача со вставленным текстом сохраняется")


class TestInlineDetails(Base):
    """Клик по облачку: в карточке всё описание и все комментарии с оформлением (без отдельного окна)."""

    def widget(self, app, iid):
        cache = app._detail_cache.get(iid)
        return cache["widget"] if cache and cache["widget"].winfo_exists() else None

    def open_card(self, app, iid="OE-1"):
        idx = self.ids(app).index(iid)
        self.click(app, f"i{idx}")
        wait_for(app, lambda: self.widget(app, iid) is not None and self.widget(app, iid).winfo_ismapped(),
                 what="содержимое в карточке")
        pump(app, 0.4)
        return self.widget(app, iid)

    def test_markup_all_comments_and_height(self):
        mock.State.issues[0]["comments"] += [mock.cm(1100 + k, text=f"Комментарий номер {k}") for k in range(5)]
        app = self.make_app()
        t = self.open_card(app)
        body = t.get("1.0", "end-1c")
        self.assertIn("Нужно обновить выгрузку", body, "**жирный** без звёздочек")
        self.assertNotIn("**", body)
        self.assertIn("Комментарии (6)", body, "все комментарии, а не три")
        self.assertLess(body.index("Комментарий номер 4"), body.index("Комментарий номер 0"), "новые сверху")
        for tag in ("b", "pre", "link"):
            self.assertTrue(t.tag_ranges(tag), tag)
        self.assertEqual(str(t.cget("state")), "disabled", "только чтение — выделять и копировать можно")
        # каждый комментарий — в своём «облачке»: 6 отдельных блоков с фоном, описание вне облачков
        ranges = t.tag_ranges("bubble")
        self.assertEqual(len(ranges) // 2, 6, "облачка не сливаются")
        first = t.get(ranges[0], ranges[1])
        self.assertIn("Комментарий номер 4", first)
        self.assertNotIn("Комментарий номер 3", first)
        self.assertNotIn("bubble", t.tag_names(t.search("Нужно", "1.0")), "описание не в облачке")
        # высота поля подогнана под содержимое: внизу ничего не обрезано
        cv = app.canvas
        item = cv.find_withtag("d0")[0]
        h = int(float(cv.itemcget(item, "height")))
        res = t.count("1.0", "end", "update", "ypixels")
        need = res[0] if isinstance(res, tuple) else res
        self.assertGreaterEqual(h, need)
        self.assertLess(h - need, 30)
        # ссылки «Написать комментарий» и «Открыть в YouTrack» — под содержимым
        link_top = cv.bbox(cv.find_withtag("o0")[0])[1]
        self.assertGreater(link_top, cv.bbox(item)[3] - 1)
        self.assertNotIn("Полностью", "\n".join(self.texts(app, "c0")))

    def test_resize_refits_and_title_opens_browser(self):
        app = self.make_app()
        t = self.open_card(app)
        cv = app.canvas
        h1 = int(float(cv.itemcget(cv.find_withtag("d0")[0], "height")))
        app.geometry("360x700")
        pump(app, 0.8)
        h2 = int(float(cv.itemcget(cv.find_withtag("d0")[0], "height")))
        self.assertGreater(h2, h1, "в узком окне текст переносится — поле выше")
        self.assertTrue(t.winfo_ismapped())
        self.click(app, "t0")
        self.assertEqual(self.opened, [f"{self.base}/issue/OE-1"], "название открывает задачу в браузере")
        # свернуть — поле убирается
        self.click(app, "i0")
        pump(app, 0.2)
        self.assertFalse(cv.find_withtag("d0"))

    def test_markdown_off_and_error(self):
        app = self.make_app(markdown=False)
        t = self.open_card(app)
        self.assertIn("**обновить**", t.get("1.0", "end-1c"), "без оформления — текст как есть")
        app.details.pop("OE-2", None)
        mock.control(self.base, "/test/mode?m=500")
        idx = self.ids(app).index("OE-2")
        self.click(app, f"i{idx}")
        wait_for(app, lambda: isinstance(app.details.get("OE-2"), Exception), what="ошибка загрузки")
        pump(app, 0.2)
        self.assertTrue(any("Не удалось загрузить" in x for x in self.texts(app, f"c{self.ids(app).index('OE-2')}")))
        self.assertTrue(any("содержимое задачи" in e for e in w.log_entries()), "ошибка записана в журнал")

    def test_theme_rebuild_keeps_details(self):
        app = self.make_app()
        self.open_card(app)
        w.apply_theme("#eef0f3", "#ffffff", 12)
        app.rebuild()
        wait_for(app, lambda: self.widget(app, "OE-1") is not None and self.widget(app, "OE-1").winfo_ismapped(),
                 what="содержимое после смены темы")
        self.assertEqual(self.widget(app, "OE-1").cget("bg"), "#ffffff")
        w.apply_theme("#1f1f1f", "#2b2b2b", 12)


class TestWatchedNotifications(Base):
    """Задачи под наблюдением и уведомления по видам (как в Android-версии)."""

    def refresh(self, app):
        app.refresh()
        wait_for(app, lambda: not app.loading, what="обновление")
        pump(app, 0.4)  # автор смены статуса загружается в фоне

    def test_status_change_by_colleague_and_own(self):
        app = self.make_app()
        self.assertEqual(self.toasts, [])
        mock.control(self.base, "/test/watch-status")
        self.refresh(app)
        wait_for(app, lambda: self.toasts, what="уведомление о смене статуса")
        self.assertEqual(self.toasts[-1][:2], ("Статус: В разработке · WI-1", "Павел Драничников: Анализ → В разработке"))
        mock.control(self.base, "/test/watch-status-mine")
        self.refresh(app)
        pump(app, 0.5)
        self.assertEqual(len(self.toasts), 1, "о своей смене статуса не уведомляем")

    def test_watched_comment_and_kind_switches(self):
        app = self.make_app(notify_comments=False)
        mock.control(self.base, "/test/watch-comment")
        self.refresh(app)
        self.assertEqual(self.toasts, [], "комментарии выключены отдельным переключателем")
        app.cfg["notify_comments"] = True
        mock.control(self.base, "/test/watch-comment")
        self.refresh(app)
        self.assertEqual([t[0] for t in self.toasts], ["Комментарий · WI-1"])
        app.cfg["notify_status"] = False
        mock.control(self.base, "/test/watch-status")
        self.refresh(app)
        pump(app, 0.4)
        self.assertEqual(len(self.toasts), 1, "смена статуса выключена")

    def test_watch_query_error_logged_and_main_list_ok(self):
        app = self.make_app(watch_query="")
        self.assertEqual(len(app.cards), 6)
        self.assertNotIn("tag: Star", " ".join(mock.State.requests), "пустой запрос — наблюдение выключено")


class TestLogAndPlanMenu(Base):
    def setUp(self):
        super().setUp()
        self._popup = w.tk.Menu.tk_popup
        w.tk.Menu.tk_popup = lambda *a, **k: None
        self.addCleanup(setattr, w.tk.Menu, "tk_popup", self._popup)
        if os.path.exists(w.LOG_PATH):
            os.remove(w.LOG_PATH)

    def test_ui_error_logged_and_journal_window(self):
        app = self.make_app()
        del app.report_callback_exception  # настоящий обработчик виджета, а не перехват тестов
        try:
            raise ValueError("тестовая ошибка с токеном perm:SECRET123 и адресом https://x.example/api")
        except ValueError as ex:
            app.report_callback_exception(ValueError, ex, ex.__traceback__)
        self.assertIn("журнал", app.status_lbl.cget("text"))
        entries = w.log_entries()
        self.assertEqual(len(entries), 1)
        self.assertIn("ERROR · интерфейс · " + w.VERSION, entries[0])
        self.assertNotIn("SECRET123", entries[0], "токен в журнал не попадает")
        self.assertNotIn("x.example", entries[0], "адрес в журнал не попадает")
        app.open_log_window()
        text = app._log_win._text.get("1.0", "end-1c")
        self.assertIn("YouTrack Widget " + w.VERSION, text)
        self.assertIn("тестовая ошибка", text)
        app._log_win.destroy()

    def test_crash_prompt_and_send(self):
        desk = os.path.join(os.environ["APPDATA"], "desk")
        os.makedirs(desk, exist_ok=True)
        popen = w.subprocess.Popen
        w.subprocess.Popen = lambda *a, **k: None  # не открывать Проводник
        self.addCleanup(setattr, w.subprocess, "Popen", popen)
        w.log_event("CRASH", "приложение", "RuntimeError: упало")
        app = self.make_app(crash_pending="RuntimeError: упало")
        app._desktop_dir = lambda: desk
        app._offer_crash_report()
        files = os.listdir(desk)
        self.assertEqual(len(files), 1, "журнал сохранён для отправки")
        with open(os.path.join(desk, files[0]), encoding="utf-8") as f:
            report = f.read()
        self.assertIn("CRASH · приложение", report)
        self.assertNotIn("crash_pending", w.load_cfg(), "предложение показывается один раз")

    def test_log_trim(self):
        os.makedirs(w.CFG_DIR, exist_ok=True)
        for k in range(1200):
            w.log_event("WARN", "тест", "x" * 300 + f" запись {k}")
        self.assertLessEqual(os.path.getsize(w.LOG_PATH), w.LOG_MAX + 1000)
        self.assertTrue(w.log_entries()[0].endswith("запись 1199"), "новые записи сохраняются")
        self.assertTrue(w.log_entries()[-1].startswith("=== "), "обрезано по границе записи")

    def test_plan_up_down_menu_and_comment_limit(self):
        w.save_plan(["OE-1", "OE-2", "OE-3"], set())
        app = self.make_app(tab="day")
        app._card_menu(KeyEv(app, 0, ""), self.card(app, "OE-2")["issue"])
        self.assertEqual(app.menu.entrycget(app.menu_up_index, "state"), "normal")
        app.menu.invoke(app.menu_up_index)  # ↑ Выше в плане
        self.assertEqual(self.ids(app), ["OE-2", "OE-1", "OE-3"])
        app._card_menu(KeyEv(app, 0, ""), self.card(app, "OE-2")["issue"])
        self.assertEqual(app.menu.entrycget(app.menu_up_index, "state"), "disabled", "первая — выше некуда")
        app.menu.invoke(app.menu_up_index + 1)  # ↓ Ниже
        self.assertEqual(self.ids(app), ["OE-1", "OE-2", "OE-3"])
        # комментарий: не длиннее 10 000 символов, в том числе при вставке
        app.open_comment_editor(self.card(app, "OE-1")["issue"])
        pump(app, 0.2)
        text = next(ch for ch in app._comment_win.winfo_children() if isinstance(ch, w.tk.Text))
        text.insert("1.0", "к" * 12_000)
        pump(app, 0.05)
        self.assertEqual(len(text.get("1.0", "end-1c")), w.COMMENT_MAX)

    def test_settings_version_and_notification_kinds(self):
        app = self.make_app()
        app.open_settings()
        win = app._settings
        pump(app, 0.2)

        def walk(r):
            for ch in r.winfo_children():
                yield ch
                yield from walk(ch)
        labels = [x.cget("text") for x in walk(win) if isinstance(x, w.tk.Label)]
        self.assertIn(f"Версия приложения: {w.VERSION}", labels)
        boxes = {x.cget("text"): x for x in walk(win) if isinstance(x, w.tk.Checkbutton)}
        for t in ("вас упомянули через @", "новый комментарий", "смена статуса (под наблюдением)",
                  "назначена новая задача", "Оформление текста как в YouTrack (полный просмотр задачи)"):
            self.assertIn(t, boxes)
        boxes["смена статуса (под наблюдением)"].invoke()  # выключить
        save = next(x for x in walk(win) if isinstance(x, w.tk.Button) and x.cget("text") == "Сохранить")
        save.invoke()
        pump(app, 0.3)
        cfg = w.load_cfg()
        self.assertFalse(cfg["notify_status"])
        self.assertTrue(cfg["notify_mentions"])
        self.assertEqual(cfg["watch_query"], "tag: Star sort by: updated")


class TestDayGroups(Base):
    """Блоки на вкладке «День»: настройка, перетаскивание между блоками, меню «Переместить в блок»."""

    def setUp(self):
        super().setUp()
        self._popup = w.tk.Menu.tk_popup
        w.tk.Menu.tk_popup = lambda *a, **k: None
        self.addCleanup(setattr, w.tk.Menu, "tk_popup", self._popup)

    def heads(self, app):
        cv = app.canvas
        return [cv.itemcget(i, "text") for i in cv.find_withtag("ghead") if cv.type(i) == "text"]

    def drag(self, app, card_idx, to_y):
        cv = app.canvas
        x1, y1, x2, y2 = cv.bbox(f"h{card_idx}")
        x, y = (x1 + x2) / 2, (y1 + y2) / 2
        app._on_press(Ev(x, y))
        steps = 12
        for k in range(1, steps + 1):
            app._on_drag(Ev(x, y + (to_y - y) * k / steps))
            app.update()
        app._on_release(Ev(x, to_y))
        pump(app, 0.1)

    def make_groups(self, app, names):
        app.open_groups_editor()
        win = app._groups_win
        pump(app, 0.1)
        win._items[0]["var"].set(names[0])
        for n in names[1:]:
            win._add()
            win._items[-1]["var"].set(n)
        win._save()
        pump(app, 0.2)

    def test_single_group_has_no_headers(self):
        w.save_plan(["OE-1", "OE-2"], set())
        app = self.make_app(tab="day")
        self.assertEqual(self.heads(app), [], "один блок — как раньше, без заголовков")
        self.assertTrue(app.groups_btn.winfo_ismapped(), "кнопка настройки блоков на вкладке «День»")

    def test_editor_drag_menu_and_steps(self):
        w.save_plan(["OE-1", "OE-2", "OE-3"], set())
        app = self.make_app(tab="day")
        self.make_groups(app, ["На сегодня", "Требуется уточнение", "Анализ"])
        self.assertEqual([g["name"] for g in w.load_plan_groups()[0]],
                         ["На сегодня", "Требуется уточнение", "Анализ"], "блоки сохранены")
        heads = self.heads(app)
        for t in ("На сегодня", "Требуется уточнение", "Анализ"):
            self.assertIn(t, heads)
        self.assertIn("Перетащите задачу сюда", self.texts(app), "пустые блоки — зона для перетаскивания")
        g = {x["name"]: x["id"] for x in app.plan_groups}
        # перетащить OE-2 в пустой блок «Анализ»
        cv = app.canvas
        empties = [i for i in cv.find_withtag("gempty") if cv.type(i) == "rectangle"]
        target = cv.bbox(empties[-1])
        self.drag(app, self.ids(app).index("OE-2"), (target[1] + target[3]) / 2)
        self.assertEqual(app.group_of("OE-2"), g["Анализ"])
        self.assertEqual(self.ids(app), ["OE-1", "OE-3", "OE-2"])
        self.assertEqual(w.load_plan_groups()[1]["OE-2"], g["Анализ"], "распределение сохранено")
        # перетащить OE-3 в «Анализ» перед OE-2
        b = app._card_bounds[self.ids(app).index("OE-2")]
        self.drag(app, self.ids(app).index("OE-3"), b[0] + 6)
        self.assertEqual(self.ids(app), ["OE-1", "OE-3", "OE-2"])
        self.assertEqual(app.group_of("OE-3"), g["Анализ"])
        self.assertEqual([c["id"] for c in app.visible if c["group"] == g["Анализ"]], ["OE-3", "OE-2"])
        # меню карточки: «Переместить в блок плана» → «Требуется уточнение»
        app._card_menu(KeyEv(app, 0, ""), self.card(app, "OE-1")["issue"])
        app._fill_group_menu()
        labels = [app.group_menu.entrycget(i, "label") for i in range(app.group_menu.index("end") + 1)
                  if app.group_menu.type(i) != "separator"]
        self.assertEqual(labels[0], "✓ На сегодня")
        app.group_menu.invoke(1)
        self.assertEqual(app.group_of("OE-1"), g["Требуется уточнение"])
        # ↓ у последней задачи блока «Требуется уточнение» — в начало «Анализа»
        app.move_plan_step(self.card(app, "OE-1")["issue"], 1)
        self.assertEqual(app.group_of("OE-1"), g["Анализ"])
        self.assertEqual([c["id"] for c in app.visible if c["group"] == g["Анализ"]], ["OE-1", "OE-3", "OE-2"])
        # ↑ — обратно в конец предыдущего блока
        app.move_plan_step(self.card(app, "OE-1")["issue"], -1)
        self.assertEqual(app.group_of("OE-1"), g["Требуется уточнение"])
        # первая задача первого блока: «выше» недоступно
        app.plan_group_of["OE-1"] = g["На сегодня"]
        app.render()
        app._card_menu(KeyEv(app, 0, ""), self.card(app, "OE-1")["issue"])
        self.assertEqual(app.menu.entrycget(app.menu_up_index, "state"), "disabled")

    def test_filter_hiding_nothing_allows_drag(self):
        """Отмечены все приоритеты (фильтр ничего не прячет) — перетаскивание работает; прячет — нет."""
        w.save_plan(["OE-1", "OE-2", "OE-3"], set())
        app = self.make_app(tab="day")
        self.make_groups(app, ["На сегодня", "Анализ", "Уточнить", "Ждёт ответа"])
        app.cfg["prio_filter"] = sorted({c["prio"] or app.NO_PRIO for c in app.cards})
        app._apply_filter()
        self.assertEqual(len(app.visible), 3)
        self.assertTrue(app._can_drag())
        self.assertIn("Перетащите задачу сюда", self.texts(app))
        g = {x["name"]: x["id"] for x in app.plan_groups}
        cv = app.canvas
        target = cv.bbox([i for i in cv.find_withtag("gempty") if cv.type(i) == "rectangle"][-1])
        self.drag(app, self.ids(app).index("OE-2"), (target[1] + target[3]) / 2)
        self.assertEqual(app.group_of("OE-2"), g["Ждёт ответа"])
        # фильтр прячет задачу плана — нельзя
        app.cfg["prio_filter"] = [app.visible[0]["prio"]]
        app._apply_filter()
        self.assertLess(len(app.visible), 3)
        self.assertFalse(app._can_drag())

    def test_add_from_all_tab_and_delete_group(self):
        app = self.make_app()
        self.make_groups(app, ["На сегодня", "Анализ"])
        g = {x["name"]: x["id"] for x in app.plan_groups}
        app._card_menu(KeyEv(app, 0, ""), self.card(app, "OE-4")["issue"])
        app._fill_group_menu()
        app.group_menu.invoke(1)  # «Анализ» — задачи не было в плане, добавилась
        self.assertIn("OE-4", app.plan)
        self.assertEqual(app.group_of("OE-4"), g["Анализ"])
        # удалить блок «Анализ» — задача переходит в первый блок
        app.open_groups_editor()
        win = app._groups_win
        win._remove(1)
        win._save()
        pump(app, 0.1)
        self.assertEqual([x["name"] for x in app.plan_groups], ["На сегодня"])
        self.assertEqual(app.group_of("OE-4"), g["На сегодня"])
        self.assertIn("OE-4", app.plan, "задача осталась в плане")

    def test_editor_validation(self):
        app = self.make_app()
        app.open_groups_editor()
        win = app._groups_win
        win._add()
        win._items[0]["var"].set("Анализ")
        win._items[1]["var"].set("анализ")
        win._save()
        self.assertTrue(win.winfo_exists(), "повтор названия не сохраняется")
        win._items[1]["var"].set("Х" * 80)
        self.assertEqual(len(win._items[1]["var"].get()), w.GROUP_NAME_MAX)
        win._save()
        self.assertFalse(win.winfo_exists())
        self.assertEqual(len(app.plan_groups), 2)


class TestResizeGrip(Base):
    """Уголок ресайза виден в окне любой высоты, и им можно растянуть окно из минимального размера."""

    def grip_visible(self, app):
        app.update()
        g = app.grip
        return g.winfo_ismapped() and g.winfo_rooty() + g.winfo_height() <= app.winfo_rooty() + app.winfo_height() + 1 \
            and g.winfo_height() >= 10

    def test_grip_at_min_height_and_resize(self):
        app = self.make_app(geometry=f"515x{w.MIN_H}+60+60")
        self.assertEqual(app.winfo_height(), w.MIN_H)
        self.assertTrue(self.grip_visible(app), "уголок ресайза виден в окне минимальной высоты")
        ev = Ev(0, 0)
        ev.x_root, ev.y_root = 500, 500
        app._resize_start(ev)
        ev.x_root, ev.y_root = 560, 800
        app._resize_move(ev)
        pump(app, 0.3)
        self.assertEqual((app.winfo_width(), app.winfo_height()), (575, w.MIN_H + 300))
        self.assertTrue(self.grip_visible(app))
        self.assertGreater(app.canvas.winfo_height(), 250, "список занял освободившееся место")


class TestCollapse(Base):
    def test_header_collapse_and_expand(self):
        app = self.make_app()
        self.assertEqual(app.title_lbl.bind("<Double-Button-1>"), "", "двойной клик по шапке больше не сворачивает")
        h = app.winfo_height()
        app.toggle_collapse()
        pump(app, 0.1)
        self.assertTrue(app.collapsed)
        self.assertEqual(app.collapse_btn.cget("text"), w.ic("expand_more"), "кнопка показывает «развернуть»")
        # перетаскивание свёрнутой шапки не разворачивает
        e1 = type("E", (), {"x_root": 500, "y_root": 300})()
        e2 = type("E", (), {"x_root": 540, "y_root": 320})()
        app._drag_start(e1)
        app._drag_end(e2)
        self.assertTrue(app.collapsed)
        # простой клик — разворачивает
        app._drag_start(e1)
        app._drag_end(e1)
        pump(app, 0.2)
        self.assertFalse(app.collapsed)
        self.assertEqual(app.winfo_height(), h)
        self.assertTrue(app.body.winfo_ismapped())
        self.assertEqual(app.collapse_btn.cget("text"), w.ic("remove"))

    def test_minimize_restore_keeps_list(self):
        app = self.make_app()
        u = ctypes.windll.user32
        hwnd = u.GetParent(app.winfo_id())
        u.ShowWindow(hwnd, 6)
        pump(app, 0.4)
        mock.control(self.base, "/test/comment")
        app.refresh()
        wait_for(app, lambda: not app.loading)
        u.ShowWindow(hwnd, 9)
        pump(app, 0.5)
        self.assertTrue(app.body.winfo_ismapped())
        self.assertEqual(len(app.visible), 6)
        self.assertGreater(len(app.canvas.find_all()), 50)


if __name__ == "__main__":
    unittest.main(verbosity=2)
