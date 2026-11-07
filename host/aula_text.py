"""Show text on the AULA F108Pro screen (patched firmware). Takes over the whole screen (mode 1).
  python aula_text.py ibok            draw text, centered, white on black
  python aula_text.py --release       give the screen back to the GIF
"""
import sys

from PIL import Image, ImageDraw, ImageFont

import aula_screen as s
import aula_timesync as t

W, H = 240, 135
FONT = r"C:\Windows\Fonts\arialbd.ttf"


def render(text, fg=(255, 255, 255)):
    size = 120
    while size > 10:
        f = ImageFont.truetype(FONT, size)
        x0, y0, x1, y1 = f.getbbox(text)
        if x1 - x0 <= W - 16 and y1 - y0 <= H - 16:
            break
        size -= 4
    im = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(im).text(((W - (x1 - x0)) // 2 - x0, (H - (y1 - y0)) // 2 - y0), text, font=f, fill=fg)
    return im


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def packets(im):
    yield s.pkt(4, 0x92, 1, 0)                          # host owns the screen
    yield s.rect(0, 0, W - 1, H - 1, 0)                 # clear
    px = im.load()
    for y in range(H):
        for x in range(0, W, 28):
            run = [rgb565(*px[xx, y]) for xx in range(x, min(x + 28, W))]
            if any(run):                                 # black chunks are already cleared
                p = s.pkt(4, 0x91, x, y, len(run))
                for i, c in enumerate(run):
                    p[5 + 2 * i], p[6 + 2 * i] = c >> 8, c & 0xFF
                yield p


def send(pks):
    path = t.find()
    if not path:
        sys.exit("keyboard not found")
    h = t.open_dev(path)
    n = 0
    try:
        for p in pks:
            for attempt in range(4):                      # retry a NACK (e.g. a DMA blit was in flight)
                ok, _ = t.xfer(h, p, delay=0.002)
                if ok:
                    break
            else:
                sys.exit("NACK on %s after retries (stock firmware?)" % bytes(p[:8]).hex())
            n += 1
    finally:
        t.k32.CloseHandle(h)
    return n


if __name__ == "__main__":
    if "--release" in sys.argv:
        send([s.pkt(4, 0x92, 0, 0)]); print("released")
    else:
        text = sys.argv[1] if len(sys.argv) > 1 else "ibok"
        im = render(text); im.save("last_text.png")
        print("sent", send(packets(im)), "packets")
