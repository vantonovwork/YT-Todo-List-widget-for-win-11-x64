"""Тесты установщика и собранных exe (в песочнице: свои папки, тестовый ключ реестра, отдельный порт)."""
import ctypes
import ctypes.wintypes as wt
import importlib.util
import json
import os
import subprocess
import time
import unittest
import winreg

from common import w, ROOT, SANDBOX
import mock_youtrack as mock

WIDGET_EXE = os.path.join(ROOT, "build", "widget", "YouTrackWidget.exe")
SETUP_EXE = os.path.join(ROOT, "dist", "YouTrackWidget-Setup.exe")


def window_titles(pid):
    titles = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        p = wt.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
            titles.append(buf.value)
        return True

    ctypes.windll.user32.EnumWindows(proto(cb), 0)
    return titles


def kill_tree(proc):
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        pass


class TestInstaller(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("inst", os.path.join(ROOT, "installer.py"))
        self.i = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.i)
        i = self.i
        i.UNINSTALL_KEY += "_AUTOTEST"
        self.desk = os.path.join(SANDBOX, "Desktop")
        os.makedirs(self.desk, exist_ok=True)
        i.desktop_dir = lambda: self.desk
        i.resource = lambda n: os.path.join(ROOT, "build", "widget", n)
        i.sys.executable = SETUP_EXE
        i.kill_running = lambda: None  # не трогать настоящий запущенный виджет пользователя
        i.messagebox.askyesno = lambda *a, **k: True
        i.messagebox.showinfo = lambda *a, **k: None
        self.addCleanup(self.cleanup_reg)

    def cleanup_reg(self):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.i.UNINSTALL_KEY)
        except FileNotFoundError:
            pass

    def test_install_move_uninstall(self):
        i = self.i
        d_drive = os.path.join(SANDBOX, "D_drive")
        os.makedirs(os.path.join(d_drive, "Apps"), exist_ok=True)
        foreign = os.path.join(d_drive, "Apps", "чужой файл.txt")
        open(foreign, "w").close()
        d1 = i.normalize_dir(os.path.join(d_drive, "Apps"))
        self.assertTrue(d1.endswith("YouTrack Widget"))
        self.assertEqual(i.normalize_dir(d1), d1)
        i.install(d1, desktop=True, autostart=True, launch=False)
        for p in (os.path.join(d1, "YouTrackWidget.exe"), os.path.join(d1, "uninstall.exe"),
                  i.STARTMENU_LNK, i.desktop_lnk(), i.STARTUP_LNK):
            self.assertTrue(os.path.exists(p), p)
        self.assertEqual(i.registered_dir(), d1)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, i.UNINSTALL_KEY) as k:
            self.assertIn("--uninstall", winreg.QueryValueEx(k, "UninstallString")[0])
        # переустановка в другую папку без ярлыка на рабочем столе и автозапуска
        d2 = i.normalize_dir(os.path.join(d_drive, "Other"))
        i.install(d2, desktop=False, autostart=False, launch=False)
        self.assertFalse(os.path.exists(d1), "старая копия удалена")
        self.assertFalse(os.path.exists(i.desktop_lnk()))
        self.assertFalse(os.path.exists(i.STARTUP_LNK))
        self.assertEqual(i.registered_dir(), d2)
        # удаление
        i.uninstall_ui()
        time.sleep(1)
        self.assertFalse(os.path.exists(d2))
        self.assertIsNone(i.registered_dir())
        self.assertFalse(os.path.exists(i.STARTMENU_LNK))
        self.assertTrue(os.path.exists(foreign), "чужие файлы не тронуты")

    def test_permission_error_message(self):
        with self.assertRaises(PermissionError):
            self.i.install(r"C:\Windows\System32\YouTrack Widget", False, False, False)


class TestBuiltExe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = mock.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_bundle_contents(self):
        from PyInstaller.archive.readers import CArchiveReader
        toc = CArchiveReader(WIDGET_EXE).toc
        for name in ("app.ico", "app.png", "fonts\\MaterialIconsRound-Regular.otf"):
            self.assertIn(name, toc)
        setup_toc = CArchiveReader(SETUP_EXE).toc
        self.assertIn("YouTrackWidget.exe", setup_toc)

    def test_widget_exe_runs(self):
        appdata = os.path.join(SANDBOX, "exe_appdata")
        cfg_dir = os.path.join(appdata, "YouTrackWidget")
        os.makedirs(cfg_dir, exist_ok=True)
        mock.control(self.base, "/test/reset")
        token = w.protect(mock.TOKEN)  # DPAPI привязан к пользователю — exe расшифрует
        with open(os.path.join(cfg_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(dict(w.DEFAULTS, base_url=self.base, token_enc=token, hotkey="", tray=True,
                           geometry="420x600+80+80", tray_hint_shown=True), f, ensure_ascii=False)
        env = dict(os.environ, APPDATA=appdata, YTW_INSTANCE_PORT="47692")
        proc = subprocess.Popen([WIDGET_EXE], env=env)
        self.addCleanup(kill_tree, proc)
        deadline = time.time() + 30
        while time.time() < deadline and not any(r.startswith("/api/issues") for r in mock.State.requests):
            time.sleep(0.3)
        self.assertTrue(any(r.startswith("/api/issues") for r in mock.State.requests), "exe загрузил задачи")
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), "exe работает")
        titles = []
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(Get-CimInstance Win32_Process -Filter \"ParentProcessId={proc.pid}\").ProcessId"],
                             capture_output=True, text=True).stdout.split()
        for pid in [proc.pid] + [int(p) for p in out if p.isdigit()]:
            titles += window_titles(pid)
        self.assertIn("YouTrack Todo", titles, "окно виджета показано")
        self.assertTrue(os.path.exists(os.path.join(cfg_dir, "seen.json")), "состояние сохранено")
        self.assertTrue(os.path.exists(os.path.join(cfg_dir, "app.png")), "иконка для уведомлений на месте")
        # второй запуск exe — не плодит копию, а завершается
        second = subprocess.Popen([WIDGET_EXE], env=env)
        self.addCleanup(kill_tree, second)
        self.assertIsNotNone(self._wait_exit(second, 20), "второй экземпляр завершился")

    def test_setup_exe_opens(self):
        proc = subprocess.Popen([SETUP_EXE])
        self.addCleanup(kill_tree, proc)
        deadline, titles = time.time() + 30, []
        while time.time() < deadline and "Установка YouTrack Widget" not in titles:
            time.sleep(0.5)
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-CimInstance Win32_Process -Filter \"ParentProcessId={proc.pid}\").ProcessId"],
                                 capture_output=True, text=True).stdout.split()
            titles = sum((window_titles(p) for p in [proc.pid] + [int(x) for x in out if x.isdigit()]), [])
        self.assertIn("Установка YouTrack Widget", titles)

    @staticmethod
    def _wait_exit(p, timeout):
        try:
            return p.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


if __name__ == "__main__":
    unittest.main(verbosity=2)
