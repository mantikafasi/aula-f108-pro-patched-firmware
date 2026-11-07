"""Show the current Windows media track on the AULA F108Pro clock page (needs firmware patch v3).
The title is sent once per track; the keyboard scrolls it smoothly by itself.
  python aula_music.py              run (Ctrl+C to stop; the overlay disappears ~5 s later)
  python aula_music.py --speed 60   title scroll speed in pixels/second (default 40, 1..200)
  python aula_music.py --print      show what would be sent, don't touch the keyboard
  python aula_music.py --test       offline self-check
Needs: pip install winrt-runtime winrt-Windows.Media.Control winrt-Windows.Foundation winrt-Windows.Foundation.Collections
"""
import asyncio, datetime, sys, time, unicodedata

import aula_screen as s
import aula_timesync as t

TTL = 5                                   # seconds the overlay lives without a refresh
TXT_MAX = 95                              # firmware title buffer
TR = str.maketrans("ıİşŞğĞçÇöÖüÜ", "iIsSgGcCoOuU")


def ascii_title(artist, title):
    if artist and title and title.lower().startswith(artist.lower()):
        artist = ""                                              # player already put the artist in the title
    txt = (f"{artist} - {title}" if artist else title or "").translate(TR)
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().strip()[:TXT_MAX]


def px(pos, dur):
    return 0 if dur <= 0 else max(0, min(240, int(240 * pos / dur)))


def title_packets(txt):                 # 04 95 off n chars (<=56 per packet), then 04 97 len to commit
    b = txt.encode("ascii")[:TXT_MAX]
    return [s.pkt(4, 0x95, off, len(b[off:off + 56]), *b[off:off + 56]) for off in range(0, len(b), 56)] + [s.pkt(4, 0x97, len(b))]


def p_show(show, ttl, pixels, speed):   # 04 94 show ttl px speed
    return s.pkt(4, 0x94, show, ttl, pixels, speed)


async def now_playing():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
    ses = (await M.request_async()).get_current_session()
    if not ses:
        return None
    info = await ses.try_get_media_properties_async()
    tl, pb = ses.get_timeline_properties(), ses.get_playback_info()
    playing = int(pb.playback_status) == 4                       # 4 = PLAYING, 5 = PAUSED
    pos = tl.position.total_seconds()
    if playing:                                                   # Windows only refreshes position now and then
        pos += (datetime.datetime.now(datetime.timezone.utc) - tl.last_updated_time).total_seconds()
    dur = (tl.end_time - tl.start_time).total_seconds()
    return ascii_title(info.artist, info.title), pos, dur, int(pb.playback_status) in (4, 5)


def selftest():
    assert ascii_title("Sezen Aksu", "Şimdi Çöl Işığı") == "Sezen Aksu - Simdi Col Isigi"
    assert ascii_title("Artist", "Artist - Song") == "Artist - Song"
    assert len(ascii_title("a", "x" * 300)) == TXT_MAX
    assert px(30, 120) == 60 and px(5, 0) == 0 and px(999, 10) == 240
    pk = title_packets("Hi")
    assert [bytes(p[:6]) for p in pk] == [bytes([4, 0x95, 0, 2, ord("H"), ord("i")]), bytes([4, 0x97, 2, 0, 0, 0])]
    long = title_packets("y" * 95)
    assert [(p[1], p[2], p[3]) for p in long] == [(0x95, 0, 56), (0x95, 56, 39), (0x97, 95, 0)]
    assert bytes(p_show(1, 5, 60, 40)[:6]) == bytes([4, 0x94, 1, 5, 60, 40])
    print("selftest ok")


async def main(dry, speed):
    h, last = None, None
    while True:
        try:
            cur = await now_playing()
        except Exception as e:                                    # no media session / winrt hiccup
            cur = None; print("media:", e)
        pk = []
        if cur and cur[3] and cur[0]:
            if cur[0] != last:
                pk += title_packets(cur[0]); last = cur[0]
            pk.append(p_show(1, TTL, px(cur[1], cur[2]), speed))
        elif last is not None:
            pk.append(p_show(0, 0, 0, 0)); last = None            # nothing playing: erase now
        if dry:
            print(time.strftime("%T"), cur, "->", [bytes(p[:6]).hex() for p in pk])
        elif pk:
            try:
                if h is None:
                    path = t.find()
                    if not path: raise OSError("keyboard not found")
                    h = t.open_dev(path)
                    ok, raw = t.xfer(h, s.pkt(4, 0x93), delay=0.002)
                    if raw[9:12] != b"PAT" or raw[12] < 3:
                        sys.exit("keyboard needs the v3 patched firmware (probe says %r): flash it first" % raw[9:13])
                for p in pk:
                    ok, _ = t.xfer(h, p, delay=0.002)
                    if not ok: print("NACK", bytes(p[:6]).hex())
            except OSError as e:                                  # unplugged / asleep: retry next second
                print("keyboard:", e)
                if h: t.k32.CloseHandle(h)
                h, last = None, None
        await asyncio.sleep(1)


if __name__ == "__main__":
    if "--test" in sys.argv:
        selftest()
    else:
        sp = int(sys.argv[sys.argv.index("--speed") + 1]) if "--speed" in sys.argv else 40
        if not 1 <= sp <= 200: sys.exit("--speed must be 1..200")
        try:
            asyncio.run(main("--print" in sys.argv, sp))
        except KeyboardInterrupt:
            pass
