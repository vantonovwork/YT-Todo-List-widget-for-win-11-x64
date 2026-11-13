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
        for p in (w.PLAN_PATH, w.SEEN_PATH, w.URGENT_PATH):
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
        base["geometry"] = "520x700+60+60"  # широкое окно — полные названия вкладок
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
        app.search.focus_force()
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
        # при включённом поиске перетаскивать нельзя
        app.search.event_generate("<FocusIn>")
        app.search_var.set("срок")
        app._apply_filter_now()
        before = list(app.plan)
        x1, y1, x2, y2 = cv.bbox("h0")
        app._on_press(Ev((x1 + x2) / 2, (y1 + y2) / 2))
        app._on_drag(Ev((x1 + x2) / 2, y2 + 80))
        app._on_release(Ev((x1 + x2) / 2, y2 + 80))
        self.assertEqual(app.plan, before)
        self.assertIn("Сбросьте поиск", app.status_lbl.cget("text"))
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
        wait_for(app, lambda: isinstance(app.details.get("OE-1"), str), what="описание")
        pump(app, 0.1)
        texts = "\n".join(self.texts(app, "c0"))
        self.assertIn("Нужно обновить выгрузку", texts)
        self.assertIn("[код]", texts)
        self.assertIn("Илья Гаврилов", texts)
        self.assertIn("Открыть в YouTrack", texts)
        self.assertEqual(app.count_lbl.cget("text"), "", "просмотр помечает комментарии прочитанными")
        self.click(app, "o0")
        self.assertEqual(self.opened[-1], f"{self.base}/issue/OE-1")
        self.click(app, "i0")
        self.assertNotIn("OE-1", app.expanded)

    def test_title_click_opens_and_marks_read(self):
        w.save_seen({"OE-1": 0})
        app = self.make_app()
        self.click(app, "t0")
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
        self.assertEqual(titles, ["Комментарий · OE-2", "Новая задача · OE-9"])
        self.assertEqual(self.toasts[0][1], "Илья Гаврилов: Нужна проверка на тесте, см. лог")
        self.assertEqual(self.toasts[0][2], f"{self.base}/issue/OE-2")
        self.refresh(app)
        self.assertEqual(len(self.toasts), 2, "повторно об одном и том же не уведомляем")
        mock.control(self.base, "/test/many-comments")
        self.refresh(app)
        self.assertEqual(len(self.toasts), 3)
        self.assertIn("новых комментариев: 5", self.toasts[-1][0], "много событий — одно сводное уведомление")

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
