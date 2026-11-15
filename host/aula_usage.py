"""Show Claude plan usage (5-hour and weekly limits) as two rings on the AULA F108Pro clock page (needs firmware v5).
  python aula_usage.py --print    fetch and show what would be sent, don't touch the keyboard
  python aula_usage.py            send now and keep refreshing (Ctrl+C to stop; the stock icons return ~2.5 min later)
  python aula_usage.py --hide     give the icons back now
  python aula_usage.py --test     offline self-check
Source: Claude Code's login (~/.claude/.credentials.json) -> https://api.anthropic.com/api/oauth/usage. That endpoint is
undocumented and could change. The token is only sent to api.anthropic.com. It is never refreshed by this code: when it has
expired, `claude auth status` is run, which makes Claude Code renew it. If that fails, run `claude auth login` once.
"""
import datetime, json, os, subprocess, sys, time, urllib.request

import aula_screen as s
import aula_timesync as t

TTL = 150                                                        # seconds the rings live without a refresh
CRED = os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json")
URL = "https://api.anthropic.com/api/oauth/usage"


def login():
    return json.load(open(CRED, encoding="utf-8"))["claudeAiOauth"]


def fetch():
    """-> (five_hour_pct, weekly_pct, five_hour_seconds_left, weekly_seconds_left); raises on any problem"""
    tok = login()
    if tok.get("expiresAt", 0) / 1000 < time.time():                # let Claude Code itself renew it (no model call, no usage)
        subprocess.run(["claude", "auth", "status"], capture_output=True, timeout=60, shell=os.name == "nt")
        tok = login()
        if tok.get("expiresAt", 0) / 1000 < time.time():
            raise RuntimeError("Claude Code login has expired: run `claude auth login` once")
    req = urllib.request.Request(URL, headers={"Authorization": "Bearer " + tok["accessToken"], "anthropic-beta": "oauth-2025-04-20",
                                               "User-Agent": "aula-f108pro-usage"})
    d = json.load(urllib.request.urlopen(req, timeout=15))
    return parse(d)


def parse(d, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for k in ("five_hour", "seven_day"):
        w = d[k]
        left = (datetime.datetime.fromisoformat(w["resets_at"].replace("Z", "+00:00")) - now).total_seconds() if w.get("resets_at") else 0
        out.append((round(w["utilization"]), max(0, left)))
    return out[0][0], out[1][0], out[0][1], out[1][1]


def hm(sec):                                                      # 5h window: "2:14", or "45m" under an hour
    m = int(sec // 60)
    return "%dm" % m if m < 60 else "%d:%02d" % (m // 60, m % 60)


def dh(sec):                                                      # weekly window: "3d", or "19h" under two days
    h = int(sec // 3600)
    return "%dd" % (h // 24) if h >= 48 else "%dh" % h


def packet(v, show=1):
    p5, pw, r5, rw = v
    pad = lambda x: x.encode("ascii")[:4].ljust(4, b"\0")
    return s.pkt(4, 0x99, show, TTL, min(p5, 100), min(pw, 100), *pad(hm(r5)), *pad(dh(rw)))


def selftest():
    now = datetime.datetime(2026, 11, 15, 12, 0, tzinfo=datetime.timezone.utc)
    d = {"five_hour": {"utilization": 33.6, "resets_at": "2026-11-15T14:14:30Z"},
         "seven_day": {"utilization": 83.0, "resets_at": "2026-11-18T12:00:00.123Z"}}
    v = parse(d, now)
    assert v[:2] == (34, 83) and hm(v[2]) == "2:14" and dh(v[3]) == "3d", v
    assert hm(45 * 60) == "45m" and hm(0) == "0m" and dh(19 * 3600) == "19h" and dh(47 * 3600) == "47h"
    assert bytes(packet((34, 83, 8040, 259200))[:14]) == bytes([4, 0x99, 1, TTL, 34, 83]) + b"2:14" + b"3d\0\0"
    assert packet((150, 7, 0, 0))[4] == 100
    assert parse({"five_hour": {"utilization": 5, "resets_at": None}, "seven_day": {"utilization": 6, "resets_at": None}}, now)[2:] == (0, 0)
    print("selftest ok")


def main():
    if "--test" in sys.argv: return selftest()
    if "--print" in sys.argv:
        v = fetch(); print("5h %d%% (resets in %s)   week %d%% (resets in %s)" % (v[0], hm(v[2]), v[1], dh(v[3]))); return
    path = t.find()
    if not path: sys.exit("keyboard not found")
    h = t.open_dev(path)
    if s.probe(h) < 5: sys.exit("needs the patched firmware v5 (flash it first)")
    try:
        if "--hide" in sys.argv:
            return print("hidden" if t.xfer(h, s.pkt(4, 0x99, 0), delay=0.002)[0] else "no ack")
        while True:
            try:
                ok, _ = t.xfer(h, packet(fetch()), delay=0.002); print(time.strftime("%T"), "sent" if ok else "NACK")
            except Exception as e:
                print(time.strftime("%T"), "usage:", e)
            time.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        t.k32.CloseHandle(h)


if __name__ == "__main__":
    main()
