"""AULA F108Pro background service: now-playing overlay + periodic clock resync, in ONE process (one writer).

  python host/aula_daemon.py --install [options]   start automatically at Windows logon (per-user, no admin)
  python host/aula_daemon.py --uninstall           remove the autostart entry
  python host/aula_daemon.py --status              autostart entry, running instance, last log lines
  python host/aula_daemon.py --stop                stop the running instance
  python host/aula_daemon.py [options]             run now (no window with pythonw; --foreground to see the log)

Options: --speed N (title px/s, default 40)   --sync-hours H (default 6; the keyboard clock drifts)
         --no-music   --no-sync   --foreground
It syncs the clock whenever the keyboard (re)connects and then every H hours. It pauses itself while the AULA vendor app
(DeviceDriver.exe) is running, so the two never write to the keyboard at the same time. Works on stock firmware too
(clock sync only; the overlay needs the patched firmware v3+). Log: %LOCALAPPDATA%\\aula-f108pro\\daemon.log
"""
import argparse, asyncio, ctypes, msvcrt, os, subprocess, sys, time
from ctypes import wintypes as w

import aula_music as m
import aula_screen as s
import aula_timesync as t

APP = "AULAF108ProDaemon"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DATA = os.path.join(os.environ.get("LOCALAPPDATA", "."), "aula-f108pro")
LOG, LOCK = os.path.join(DATA, "daemon.log"), os.path.join(DATA, "daemon.lock")
LOCK_BYTE = 1024
VENDOR_EXE = "devicedriver.exe"
FOREGROUND = False


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    if FOREGROUND:
        print(line, flush=True)
    try:
        os.makedirs(DATA, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > 200_000:        # keep it small: drop the older half
            tail = open(LOG, "rb").read()[-100_000:]
            open(LOG, "wb").write(tail)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", w.DWORD), ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
                ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260)]


def process_names():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = w.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    k.Process32FirstW.argtypes = k.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k.CloseHandle.argtypes = [w.HANDLE]
    snap = k.CreateToolhelp32Snapshot(2, 0)                                # TH32CS_SNAPPROCESS
    names, e = set(), PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
    ok = k.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        names.add(e.szExeFile.lower()); ok = k.Process32NextW(snap, ctypes.byref(e))
    k.CloseHandle(snap)
    return names


def lock_or_exit():
    os.makedirs(DATA, exist_ok=True)
    f = open(LOCK, "a+")
    try:
        f.seek(LOCK_BYTE); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)   # lock a byte AFTER the pid text: locked bytes can't be read
    except OSError:
        sys.exit("already running (see --status / --stop)")
    f.seek(0); f.truncate(0); f.write(str(os.getpid()).ljust(16)); f.flush()
    return f                                                               # keep referenced: the lock lives with the handle


def running_pid():
    try:
        pid = int(open(LOCK).read().strip())
    except (OSError, ValueError):
        return None
    probe = open(LOCK, "a+")
    try:
        probe.seek(LOCK_BYTE); msvcrt.locking(probe.fileno(), msvcrt.LK_NBLCK, 1); msvcrt.locking(probe.fileno(), msvcrt.LK_UNLCK, 1)
        return None                                                        # we could take the lock: nobody holds it
    except OSError:
        return pid
    finally:
        probe.close()


def command_line(args):
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.exists(pyw) else sys.executable
    cmd = f'"{exe}" "{os.path.abspath(__file__)}" --speed {args.speed} --sync-hours {args.sync_hours}'
    return cmd + (" --no-music" if args.no_music else "") + (" --no-sync" if args.no_sync else "")


def reg_get():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, APP)[0]
    except OSError:
        return None


async def run(args):
    h, path, overlay, last_title, last_sync, paused = None, None, False, None, 0.0, False
    log("service started: music=%s sync=%s every %sh speed=%s" % (not args.no_music, not args.no_sync, args.sync_hours, args.speed))
    while True:
        try:
            if VENDOR_EXE in process_names():                              # never write while the vendor app configures the keyboard
                if h:
                    t.k32.CloseHandle(h); h = None; last_title = None
                if not paused:
                    log("vendor app is running: pausing"); paused = True
                await asyncio.sleep(2); continue
            if paused:
                log("vendor app closed: resuming"); paused = False
            if h is None:
                path = t.find()
                if not path:
                    await asyncio.sleep(5); continue
                h = t.open_dev(path)
                ver = s.probe(h)
                overlay = ver >= 3
                last_title, last_sync = None, 0.0                          # new connection: resend title, resync clock now
                log("keyboard connected (%s)" % ("patched firmware v%d" % ver if ver else "stock firmware, clock sync only"))
            if not args.no_sync and time.time() - last_sync >= args.sync_hours * 3600:
                ok = t.sync(path)
                last_sync = time.time()
                log("clock sync %s" % ("ok" if ok else "sent, but not acknowledged"))
            if overlay and not args.no_music:
                try:
                    cur = await m.now_playing()
                except Exception as e:                                     # no media session / winrt hiccup
                    cur = None; log("media: %s" % e)
                pk = []
                if cur and cur[3] and cur[0]:
                    if cur[0] != last_title:
                        pk += m.title_packets(cur[0]); last_title = cur[0]
                    pk.append(m.p_show(1, m.TTL, m.px(cur[1], cur[2]), args.speed))
                elif last_title is not None:
                    pk.append(m.p_show(0, 0, 0, 0)); last_title = None
                for p in pk:
                    ok, _ = t.xfer(h, p, delay=0.002)
                    if not ok:
                        log("NACK on %s" % bytes(p[:6]).hex())
        except OSError as e:                                               # unplugged / asleep / resumed from sleep
            log("keyboard: %s" % e)
            if h:
                try: t.k32.CloseHandle(h)
                except Exception: pass
            h, last_title = None, None
            await asyncio.sleep(3)
        await asyncio.sleep(1)


def selftest():
    a = argparse.Namespace(speed=50, sync_hours=4, no_music=True, no_sync=False)
    cmd = command_line(a)
    assert cmd.startswith('"') and "--speed 50" in cmd and "--sync-hours 4" in cmd and "--no-music" in cmd and "--no-sync" not in cmd
    assert any(n.startswith("python") for n in process_names()), "process enumeration"
    assert VENDOR_EXE == VENDOR_EXE.lower()
    print("selftest ok")


def main():
    global FOREGROUND
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--install", action="store_true"); ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true"); ap.add_argument("--stop", action="store_true")
    ap.add_argument("--test", action="store_true"); ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--speed", type=int, default=40); ap.add_argument("--sync-hours", type=float, default=6)
    ap.add_argument("--no-music", action="store_true"); ap.add_argument("--no-sync", action="store_true")
    a = ap.parse_args()
    if not 1 <= a.speed <= 200 or not 0.05 <= a.sync_hours <= 168:
        sys.exit("--speed must be 1..200 and --sync-hours 0.05..168")
    if a.test:
        return selftest()
    if a.install or a.uninstall:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if a.install:
                winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, command_line(a))
                print("autostart installed (HKCU Run):", command_line(a))
            else:
                try: winreg.DeleteValue(k, APP); print("autostart removed")
                except OSError: print("no autostart entry")
        return
    if a.status:
        print("autostart:", reg_get() or "not installed")
        pid = running_pid(); print("running: pid %s" % pid if pid else "running: no")
        if os.path.exists(LOG):
            print("--- last log lines:"); print("".join(open(LOG, encoding="utf-8").readlines()[-8:]), end="")
        return
    if a.stop:
        pid = running_pid()
        if not pid: return print("not running")
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False); return print("stopped pid", pid)
    FOREGROUND = a.foreground
    if sys.stdout is None: sys.stdout = open(os.devnull, "w")
    if sys.stderr is None: sys.stderr = open(os.devnull, "w")
    lock = lock_or_exit()
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        log("stopped (Ctrl+C)")


if __name__ == "__main__":
    main()
