"""Sync the AULA F108Pro screen clock over USB without the vendor app.

Protocol lifted statically from DeviceDriver.exe (SyncTime @0x423b10, wired/USB path).
  python aula_timesync.py            sync once
  python aula_timesync.py --dry-run  find the device, print packets, send nothing
  python aula_timesync.py --watch    sync on plug-in, then every 6 h (run at logon)
  python aula_timesync.py --test     offline packet self-check
Needs: pip install hidapi   (only used to list HID paths)
"""
import ctypes, datetime, sys, time
from ctypes import wintypes as w

import hid

VID, PID = 0x0C45, 0x800A   # config.xml, USB mode. 2.4G dongle (05AC:024F) is NOT handled.
SLOT = 1                    # vendor sends LCDViewList.GetCurSel()+1; only 1 GIF slot exists (gif_count=1)
DELAY = 0.035               # layouts/rgb-keyboard.xml cmd_delaytime=35 ms
EVERY = 6 * 3600            # ponytail: fixed re-sync period, make it a flag if drift matters

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
hidl = ctypes.WinDLL("hid", use_last_error=True)
k32.CreateFileW.restype = w.HANDLE
k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
k32.CloseHandle.argtypes = [w.HANDLE]
k32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, w.DWORD,
                                ctypes.POINTER(w.DWORD), w.LPVOID]
hidl.HidD_SetFeature.argtypes = [w.HANDLE, w.LPVOID, w.ULONG]
hidl.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hidl.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hidl.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
hidl.HidP_GetCaps.restype = ctypes.c_long
BAD = ctypes.c_void_p(-1).value


def packets(now):
    """The four 64-byte feature-report payloads (report ID 0 is prepended on send)."""
    def cmd(*b):
        p = bytearray(64); p[:len(b)] = b; return p
    t = cmd(0, SLOT, 0x5A, now.year % 2000, now.month, now.day, now.hour, now.minute, now.second)
    t[10] = now.isoweekday() % 7            # SYSTEMTIME.wDayOfWeek: Sunday=0
    t[62:64] = b"\xAA\x55"
    arm = cmd(0x04, 0x28); arm[8] = 1
    return [cmd(0x04, 0x18), arm, t, cmd(0x04, 0x02)]


def feature_len(path):
    h = k32.CreateFileW(path, 0, 3, None, 3, 0, None)   # access 0: just ask for caps
    if h in (None, BAD):
        return 0
    n, pp = 0, ctypes.c_void_p()
    if hidl.HidD_GetPreparsedData(h, ctypes.byref(pp)):
        caps = (ctypes.c_ushort * 32)()                  # HIDP_CAPS = 64 bytes
        if hidl.HidP_GetCaps(pp, caps) == 0x110000:
            n = caps[4]                                  # FeatureReportByteLength
        hidl.HidD_FreePreparsedData(pp)
    k32.CloseHandle(h)
    return n


def find():
    """Vendor picks by report size (65-byte feature report), not by interface number.
    On the dev unit that is MI_03 / usage page 0xFF13, although config.xml says MI_00."""
    for d in hid.enumerate(VID, PID):
        p = d["path"].decode() if isinstance(d["path"], bytes) else d["path"]
        if d["usage_page"] >= 0xFF00 and feature_len(p) == 65:
            return p


def sync(path, dry=False):
    pk = packets(datetime.datetime.now())
    if dry:
        for p in pk:
            print("would send:", p.hex())
        return True
    h = open_dev(path)
    ok = True
    try:
        for p in pk:
            acked, raw = xfer(h, p)
            ok &= acked
            print(p[:2].hex(), "ack" if acked else "NO ACK, reply=" + raw[:12].hex())
    finally:
        k32.CloseHandle(h)
    return ok


def open_dev(path):
    h = k32.CreateFileW(path, 0x40000000, 3, None, 3, 0, None)   # GENERIC_WRITE only, like the vendor
    if h in (None, BAD):
        raise OSError(ctypes.get_last_error(), "CreateFile " + path)
    return h


def xfer(h, p, delay=DELAY, strict=False):
    """SetFeature one 64-byte packet, read it back; returns (acked, raw reply)."""
    rep = bytes([0]) + bytes(p)
    time.sleep(delay)
    if not hidl.HidD_SetFeature(h, ctypes.create_string_buffer(rep, 65), 65):
        raise OSError(ctypes.get_last_error(), "HidD_SetFeature")
    time.sleep(delay)
    out, n = ctypes.create_string_buffer(rep, 65), w.DWORD()
    k32.DeviceIoControl(h, 0xB0192, out, 65, out, 65, ctypes.byref(n), None)  # IOCTL_HID_GET_FEATURE
    # command packets reply with data[3]==1; the time packet is echoed back verbatim (seen on hardware)
    return (out.raw[4] == 1) if strict else (out.raw[4] == 1 or out.raw == rep), out.raw   # strict: ignore the unprocessed-echo rule


def selftest():
    p = packets(datetime.datetime(2026, 11, 7, 13, 5, 9))     # a Saturday
    assert p[0][:2] == b"\x04\x18" and p[3][:2] == b"\x04\x02" and p[1][8] == 1
    assert bytes(p[2][:11]) == bytes([0, 1, 0x5A, 26, 11, 7, 13, 5, 9, 0, 6])
    assert bytes(p[2][62:]) == b"\xAA\x55" and all(len(x) == 64 for x in p)
    print("selftest ok")


def main(a):
    if "--test" in a:
        return selftest()
    if "--watch" in a:
        here, last = False, 0
        while True:
            path = find()
            if path and (not here or time.time() - last > EVERY):
                try:
                    print(time.strftime("%F %T"), "sync", "ok" if sync(path) else "unacked", flush=True)
                    last = time.time()
                except OSError as e:
                    print("sync failed:", e, flush=True)
            here = bool(path)
            time.sleep(5)
    path = find()
    if not path:
        sys.exit("keyboard not found in USB mode (0C45:800A, vendor collection with 65-byte feature report)")
    print("device:", path)
    sys.exit(0 if sync(path, "--dry-run" in a) else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
