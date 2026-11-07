"""Drive the AULA F108Pro screen. Needs the PATCHED firmware (firmware_patch/); stock firmware NACKs these.
  python aula_screen.py probe             is the patched firmware running?
  python aula_screen.py bar 42            progress bar (0-100) on the bottom 7 rows; GIF keeps playing above
  python aula_screen.py release           back to stock behaviour (full-screen GIF)
  add --dry-run to print packets without sending
"""
import sys

import aula_timesync as t

STRIP_Y, CLIP = 128, 128          # rows 128..134 belong to the host once clip mode is on
FILL, EMPTY = 0x07E0, 0x2104      # green / dark grey, RGB565


def pkt(*b):
    p = bytearray(64); p[:len(b)] = b; return p


def probe(h):
    """-> patch version (int), or 0 for stock firmware. A request the keyboard has not processed yet is echoed back
    unchanged (data[3] == 0), so keep asking until it answers: 1 = ack, 0xFF = nack (stock firmware nacks 0x93)."""
    for _ in range(10):
        ok, raw = t.xfer(h, pkt(4, 0x93), delay=0.004, strict=True)
        if raw[4] in (1, 0xFF):
            return raw[12] if raw[9:12] == b"PAT" else 0
    raise OSError("keyboard did not answer the probe")


def rect(x0, y0, x1, y1, c):      # 04 90 x0 y0 x1 y1 colourH colourL
    return pkt(4, 0x90, x0, y0, x1, y1, c >> 8, c & 0xFF)


def bar(pct):
    w = round(240 * max(0, min(100, pct)) / 100)
    out = [pkt(4, 0x92, 2, CLIP)]                       # clip GIF frames so the strip isn't overwritten
    if w:       out.append(rect(0, STRIP_Y, w - 1, 134, FILL))
    if w < 240: out.append(rect(w, STRIP_Y, 239, 134, EMPTY))
    return out


def main(a):
    dry = "--dry-run" in a
    a = [x for x in a if not x.startswith("--")]
    cmd = a[0] if a else "probe"
    pk = {"probe": lambda: [pkt(4, 0x93)], "release": lambda: [pkt(4, 0x92, 0, 0)],
          "bar": lambda: bar(float(a[1]))}[cmd]()
    if cmd == "probe" and not ("--dry-run" in sys.argv):
        pk = []
    path = t.find()
    if not path:
        sys.exit("keyboard not found")
    if dry:
        for p in pk: print("would send:", bytes(p[:8]).hex(), "...")
        return
    h = t.open_dev(path)
    try:
        if cmd == "probe":
            v = probe(h); print("patched firmware v%d" % v if v else "stock firmware (no PAT signature)")
        for p in pk:
            ok, raw = t.xfer(h, p, delay=0.005)
            if not ok:
                sys.exit("NACK on %s - stock firmware, or bad arguments" % bytes(p[:8]).hex())
    finally:
        t.k32.CloseHandle(h)


if __name__ == "__main__":
    main(sys.argv[1:])
