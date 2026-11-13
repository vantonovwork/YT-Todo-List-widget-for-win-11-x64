"""Общее для тестов: изолированная папка настроек, загрузка модуля виджета, ожидание событий Tk."""
import importlib.util
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SANDBOX = tempfile.mkdtemp(prefix="ytw_tests_")
# ВАЖНО: до импорта виджета — все его файлы (настройки, план, автозапуск) уйдут в песочницу
os.environ["APPDATA"] = os.path.join(SANDBOX, "Roaming")
os.environ["LOCALAPPDATA"] = os.path.join(SANDBOX, "Local")
os.makedirs(os.environ["APPDATA"], exist_ok=True)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)


def load_widget():
    spec = importlib.util.spec_from_file_location("ytw", os.path.join(ROOT, "youtrack_widget.pyw"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


w = load_widget()


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def wait_for(app, cond, timeout=8.0, what="условие"):
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"Не дождались: {what}")
