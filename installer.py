"""
Установщик YouTrack Widget (для текущего пользователя, без прав администратора).

YouTrackWidget-Setup.exe            — установка
uninstall.exe --uninstall           — удаление (регистрируется в «Приложения и возможности»)
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
import tkinter as tk
import winreg
from tkinter import filedialog, messagebox

APP_NAME = "YouTrack Widget"
VERSION = "1.2.0"
EXE_NAME = "YouTrackWidget.exe"
LOCALAPPDATA = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
APPDATA = os.environ.get("APPDATA", os.path.expanduser(r"~\AppData\Roaming"))
INSTALL_DIR = os.path.join(LOCALAPPDATA, "Programs", APP_NAME)
STARTMENU_LNK = os.path.join(APPDATA, r"Microsoft\Windows\Start Menu\Programs", APP_NAME + ".lnk")
STARTUP_LNK = os.path.join(APPDATA, r"Microsoft\Windows\Start Menu\Programs\Startup", "YouTrack Widget.lnk")
SETTINGS_DIR = os.path.join(APPDATA, "YouTrackWidget")
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\YouTrackWidget"
NO_WINDOW = 0x08000000

BG, CARD, TEXT, SUB, ACCENT = "#1f1f1f", "#2b2b2b", "#e8e8e8", "#9d9d9d", "#0067c0"
FONT = "Segoe UI"


def resource(name):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(0, 0x10, 0, 0, buf)  # CSIDL_DESKTOPDIRECTORY (учитывает OneDrive)
    return buf.value or os.path.join(os.path.expanduser("~"), "Desktop")


def desktop_lnk():
    return os.path.join(desktop_dir(), APP_NAME + ".lnk")


def make_lnk(lnk, target):
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    env = dict(os.environ, YTW_LNK=lnk, YTW_TARGET=target, YTW_DIR=os.path.dirname(target))
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:YTW_LNK);"
          "$s.TargetPath=$env:YTW_TARGET;$s.WorkingDirectory=$env:YTW_DIR;"
          "$s.IconLocation=$env:YTW_TARGET+',0';$s.Description='YouTrack Todo';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   env=env, check=True, creationflags=NO_WINDOW)


def kill_running():
    subprocess.run(["taskkill", "/IM", EXE_NAME, "/F", "/T"], capture_output=True, creationflags=NO_WINDOW)


def retry(fn, *args, attempts=20):
    """Файл может быть занят ещё пару секунд после закрытия виджета."""
    for n in range(attempts):
        try:
            return fn(*args)
        except PermissionError:
            if n == attempts - 1:
                raise
            time.sleep(0.25)


def remove(path):
    try:
        retry(os.remove, path)
    except FileNotFoundError:
        pass


def dark_titlebar(win):
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


# ------------------------------------------------------------------ установка
def registered_dir():
    """Папка, куда программа уже установлена (из реестра), или None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            return winreg.QueryValueEx(k, "InstallLocation")[0] or None
    except OSError:
        return None


def normalize_dir(path):
    """Всегда ставим в отдельную подпапку «YouTrack Widget», чтобы не мусорить в выбранной папке."""
    path = os.path.abspath(os.path.expandvars(path.strip().strip('"')))
    if os.path.basename(path.rstrip("\\/")).lower() != APP_NAME.lower():
        path = os.path.join(path, APP_NAME)
    return path


def remove_files(install_dir, keep_running_uninstaller=False):
    """Удалить только свои файлы и папку, если она опустела (чужое не трогаем)."""
    remove(os.path.join(install_dir, EXE_NAME))
    if not keep_running_uninstaller:
        remove(os.path.join(install_dir, "uninstall.exe"))
        try:
            os.rmdir(install_dir)
        except OSError:
            pass


def install(install_dir, desktop, autostart, launch):
    kill_running()
    old = registered_dir()
    os.makedirs(install_dir, exist_ok=True)
    exe = os.path.join(install_dir, EXE_NAME)
    retry(shutil.copy2, resource(EXE_NAME), exe)
    uninst = os.path.join(install_dir, "uninstall.exe")
    if os.path.abspath(sys.executable).lower() != uninst.lower():
        retry(shutil.copy2, sys.executable, uninst)
    if old and os.path.normcase(os.path.abspath(old)) != os.path.normcase(install_dir):
        remove_files(old)  # переустановка в новую папку — убираем старую копию

    make_lnk(STARTMENU_LNK, exe)
    if desktop:
        make_lnk(desktop_lnk(), exe)
    else:
        remove(desktop_lnk())
    if autostart:
        make_lnk(STARTUP_LNK, exe)
    else:
        remove(STARTUP_LNK)

    size_kb = sum(os.path.getsize(p) for p in (exe, uninst)) // 1024
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", APP_NAME), ("DisplayVersion", VERSION),
                          ("Publisher", "YouTrack Widget"), ("DisplayIcon", exe + ",0"),
                          ("InstallLocation", install_dir),
                          ("UninstallString", f'"{uninst}" --uninstall')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        for name, val in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", size_kb)):
            winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, val)

    if launch:
        subprocess.Popen([exe], cwd=install_dir, close_fds=True)


def install_ui():
    root = tk.Tk()
    root.title(f"Установка {APP_NAME}")
    root.configure(bg=BG, padx=24, pady=18)
    root.resizable(False, False)
    try:
        root.iconbitmap(default=resource("app.ico"))
    except tk.TclError:
        pass
    dark_titlebar(root)

    tk.Label(root, text=APP_NAME, bg=BG, fg=TEXT, font=(FONT, 16, "bold")).pack(anchor="w")
    tk.Label(root, text="Задачи из YouTrack на рабочем столе Windows", bg=BG, fg=SUB,
             font=(FONT, 9)).pack(anchor="w", pady=(0, 14))
    tk.Label(root, text="Папка установки:", bg=BG, fg=TEXT, font=(FONT, 9)).pack(anchor="w")
    dir_row = tk.Frame(root, bg=BG)
    dir_row.pack(anchor="w", fill="x", pady=(2, 2))
    v_dir = tk.StringVar(value=registered_dir() or INSTALL_DIR)
    e_dir = tk.Entry(dir_row, textvariable=v_dir, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat",
                     font=(FONT, 9), width=52)
    e_dir.pack(side="left", fill="x", expand=True, ipady=5, ipadx=6)

    def browse():
        start = v_dir.get()
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            start = parent if parent != start else ""
        chosen = filedialog.askdirectory(parent=root, title="Куда установить YouTrack Widget",
                                         initialdir=start or None, mustexist=False)
        if chosen:
            v_dir.set(normalize_dir(chosen))

    tk.Button(dir_row, text="Обзор…", command=browse, bg=CARD, fg=TEXT, activebackground="#333",
              activeforeground=TEXT, relief="flat", font=(FONT, 9), padx=10, bd=0, cursor="hand2").pack(
        side="left", padx=(6, 0), ipady=2)
    tk.Label(root, text="Будет создана подпапка «YouTrack Widget». Права администратора не нужны,\n"
                        "если вы выбираете обычную папку (не Program Files).",
             bg=BG, fg=SUB, font=(FONT, 8), justify="left").pack(anchor="w", pady=(0, 12))

    v_desk, v_auto, v_run = tk.BooleanVar(value=True), tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    cb = dict(bg=BG, fg=TEXT, selectcolor=CARD, activebackground=BG, activeforeground=TEXT, font=(FONT, 10))
    tk.Checkbutton(root, text="Создать ярлык на рабочем столе", variable=v_desk, **cb).pack(anchor="w")
    tk.Checkbutton(root, text="Запускать вместе с Windows", variable=v_auto, **cb).pack(anchor="w")
    tk.Checkbutton(root, text="Запустить после установки", variable=v_run, **cb).pack(anchor="w")

    status = tk.Label(root, text="", bg=BG, fg=SUB, font=(FONT, 9))
    status.pack(anchor="w", pady=(12, 0))

    btns = tk.Frame(root, bg=BG)
    btns.pack(anchor="e", pady=(8, 0))
    bstyle = dict(relief="flat", font=(FONT, 10), padx=16, pady=5, cursor="hand2", bd=0)

    def do_install():
        b_ok.configure(state="disabled")
        status.configure(text="Устанавливаю…")
        root.update()
        target = normalize_dir(v_dir.get() or INSTALL_DIR)
        v_dir.set(target)
        try:
            install(target, v_desk.get(), v_auto.get(), v_run.get())
        except PermissionError:
            messagebox.showerror("Ошибка установки", f"Нет прав на запись в папку:\n{target}\n\n"
                                 "Выберите другую папку, например на диске D:.", parent=root)
            b_ok.configure(state="normal")
            status.configure(text="")
            return
        except Exception as e:
            messagebox.showerror("Ошибка установки", str(e), parent=root)
            b_ok.configure(state="normal")
            status.configure(text="")
            return
        messagebox.showinfo(APP_NAME, "Установка завершена.\n\nВиджет есть в меню «Пуск»"
                            + (" и на рабочем столе." if v_desk.get() else "."), parent=root)
        root.destroy()

    tk.Button(btns, text="Отмена", command=root.destroy, bg=CARD, fg=TEXT, activebackground="#333",
              activeforeground=TEXT, **bstyle).pack(side="left", padx=4)
    b_ok = tk.Button(btns, text="Установить", command=do_install, bg=ACCENT, fg="white",
                     activebackground="#1975c5", activeforeground="white", **bstyle)
    b_ok.pack(side="left", padx=4)
    root.bind("<Return>", lambda e: do_install())
    root.bind("<Escape>", lambda e: root.destroy())

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.mainloop()


# ------------------------------------------------------------------ удаление
def uninstall_ui():
    root = tk.Tk()
    root.withdraw()
    if not messagebox.askyesno(f"Удаление {APP_NAME}", f"Удалить {APP_NAME}?", parent=root):
        return
    keep = not messagebox.askyesno(APP_NAME, "Удалить также настройки (адрес YouTrack и токен)?", parent=root)
    install_dir = registered_dir()
    if not install_dir and getattr(sys, "frozen", False):
        install_dir = os.path.dirname(os.path.abspath(sys.executable))
    install_dir = install_dir or INSTALL_DIR
    kill_running()
    for p in (STARTMENU_LNK, desktop_lnk(), STARTUP_LNK):
        remove(p)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except FileNotFoundError:
        pass
    if not keep:
        shutil.rmtree(SETTINGS_DIR, ignore_errors=True)
    running = os.path.normcase(os.path.abspath(sys.executable)) == \
        os.path.normcase(os.path.join(install_dir, "uninstall.exe"))
    remove_files(install_dir, keep_running_uninstaller=running)
    messagebox.showinfo(APP_NAME, "Удаление завершено.", parent=root)
    root.destroy()
    if running:
        # uninstall.exe не может удалить сам себя, пока запущен — удаляем его и пустую папку после выхода
        uninst = os.path.join(install_dir, "uninstall.exe")
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & del /f /q "{uninst}" & rmdir "{install_dir}"',
                         creationflags=NO_WINDOW, cwd=os.environ.get("TEMP", "C:\\"))


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    if "--uninstall" in sys.argv:
        uninstall_ui()
    else:
        install_ui()
