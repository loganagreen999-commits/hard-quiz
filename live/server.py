#!/usr/bin/env python3
"""Live multiplayer quiz: one laptop server, players join from their phones on the same wifi.

Phones poll /api/state about once a second; the server owns all game state.
The open-response "What is hazel?" answer is never sent to the server: the
phone only reports that the player pressed Submit.
"""
import io, json, os, random, secrets, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", 8099))
QUESTIONS = json.load(open(os.path.join(HERE, "questions.json")))
HOSTKEY = os.environ.get("HOSTKEY") or secrets.token_urlsafe(8)
ACTIVE_S = 15          # a phone that hasn't polled for this long stops blocking the game
FAST_S, MAX_PTS, MIN_PTS = 30, 1000, 500


def lan_ip():
    try:
        out = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True).stdout.split()
        return out[out.index("src") + 1]
    except Exception:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 80))
        return s.getsockname()[0]


JOIN_URL = os.environ.get("JOIN_URL") or f"http://{lan_ip()}:{PORT}/"


class Game:
    def __init__(self):
        self.lock = threading.RLock()
        self.players = {}
        self.reset()

    def reset(self):
        self.phase, self.qi, self.order, self.qstart = "lobby", -1, [], 0.0
        for p in self.players.values():
            p.update(score=0, correct=0, answers={}, hazel=False, late=None)

    # ---- helpers
    def active(self, p):
        return time.time() - p["seen"] < ACTIVE_S

    def waiting_on(self):
        if self.phase == "question":
            return [p for p in self.players.values()
                    if self.active(p) and self.qi not in p["answers"] and p["late"] != self.qi]
        if self.phase == "hazel":
            return [p for p in self.players.values() if self.active(p) and not p["hazel"]]
        return []

    def advance_if_done(self):
        if self.phase in ("question", "hazel") and not self.waiting_on():
            if self.phase == "question" and any(self.qi in p["answers"] for p in self.players.values()):
                self.phase = "reveal"
            elif self.phase == "hazel" and any(p["hazel"] for p in self.players.values()):
                self.phase = "final"

    # ---- actions
    def join(self, name):
        name = " ".join(name.split())[:16] or "Player"
        taken = {p["name"].lower() for p in self.players.values()}
        base, n = name, 2
        while name.lower() in taken:
            name = f"{base[:13]} {n}"; n += 1
        pid = secrets.token_urlsafe(9)
        self.players[pid] = dict(name=name, score=0, correct=0, answers={}, hazel=False, seen=time.time(),
                                 late=self.qi if self.phase == "question" else None)
        return pid

    def start(self):
        if not self.players:
            return
        self.reset()
        self.order = []
        for q in random.sample(QUESTIONS, len(QUESTIONS)):
            c = [q["a"], *q["w"]]
            random.shuffle(c)
            self.order.append(dict(s=q["s"], q=q["q"], c=c, a=c.index(q["a"]), x=q["x"]))
        self.qi, self.phase, self.qstart = 0, "question", time.time()

    def answer(self, pid, k):
        p = self.players.get(pid)
        if not p or self.phase != "question" or self.qi in p["answers"] or not 0 <= k < 4:
            return
        t = time.time() - self.qstart
        ok = k == self.order[self.qi]["a"]
        pts = round(MAX_PTS - (MAX_PTS - MIN_PTS) * min(t, FAST_S) / FAST_S) if ok else 0
        p["answers"][self.qi] = dict(k=k, ok=ok, pts=pts, t=round(t, 1))
        p["score"] += pts
        p["correct"] += ok
        self.advance_if_done()

    def next(self):
        if self.phase == "question":
            self.phase = "reveal"
        elif self.phase == "reveal":
            if self.qi + 1 < len(self.order):
                self.qi += 1
                self.phase, self.qstart = "question", time.time()
            else:
                self.phase = "hazel"
        elif self.phase == "hazel":
            self.phase = "final"

    # ---- view
    def ranked(self):
        return sorted(self.players.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["correct"], kv[1]["name"].lower()))

    def view(self, pid=None, host=False):
        self.advance_if_done()
        me = self.players.get(pid)
        if me:
            me["seen"] = time.time()
        waiting = {id(p) for p in self.waiting_on()}
        rows = []
        for rank, (k, p) in enumerate(self.ranked(), 1):
            a = p["answers"].get(self.qi) if self.qi >= 0 else None
            row = dict(name=p["name"], score=p["score"], correct=p["correct"], rank=rank,
                       me=k == pid, active=self.active(p), waiting=id(p) in waiting)
            if host:
                row["pid"] = k
            if self.phase == "reveal":
                row.update(pick=a["k"] if a else None, ok=bool(a and a["ok"]), pts=a["pts"] if a else 0)
            rows.append(row)
        v = dict(phase=self.phase, qi=self.qi, n=len(self.order) or len(QUESTIONS), players=rows,
                 joined=bool(me), join_url=JOIN_URL)
        if me:
            v["me"] = dict(name=me["name"], score=me["score"], hazel=me["hazel"],
                           answered=self.qi in me["answers"], late=me["late"] == self.qi)
        if self.phase in ("question", "reveal"):
            q = self.order[self.qi]
            v["q"] = dict(s=q["s"], q=q["q"], c=q["c"], elapsed=round(time.time() - self.qstart))
            if me and self.qi in me["answers"]:
                v["me"]["pick"] = me["answers"][self.qi]["k"]
            if self.phase == "reveal":
                v["q"].update(a=q["a"], x=q["x"])
                if me and self.qi in me["answers"]:
                    v["me"].update(ok=me["answers"][self.qi]["ok"], pts=me["answers"][self.qi]["pts"])
        return v


G = Game()
APP = os.path.join(HERE, "app.html")


def qr_svg(text):
    import qrcode, qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=3600" if ctype == "image/svg+xml" else "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if u.path in ("/", "/host"):
            return self.send(200, open(APP, "rb").read(), "text/html; charset=utf-8")
        if u.path == "/qr.svg":
            return self.send(200, qr_svg(JOIN_URL), "image/svg+xml")
        if u.path == "/api/state":
            host = qs.get("k", [""])[0] == HOSTKEY
            with G.lock:
                return self.send(200, G.view(qs.get("pid", [""])[0], host))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        with G.lock:
            if u.path == "/api/join":
                return self.send(200, {"pid": G.join(str(body.get("name", "")))})
            if u.path == "/api/answer":
                G.answer(str(body.get("pid", "")), int(body.get("k", -1)))
                return self.send(200, {"ok": True})
            if u.path == "/api/hazel":          # the typed answer is never sent, only "done"
                p = G.players.get(str(body.get("pid", "")))
                if p and G.phase == "hazel":
                    p["hazel"] = True
                    G.advance_if_done()
                return self.send(200, {"ok": True})
            if u.path.startswith("/api/host/"):
                if body.get("k") != HOSTKEY:
                    return self.send(403, {"error": "host key required"})
                act = u.path.rsplit("/", 1)[1]
                if act == "start":
                    G.start()
                elif act == "next":
                    G.next()
                elif act == "lobby":
                    G.reset()
                elif act == "kick":
                    G.players.pop(str(body.get("pid", "")), None)
                    G.advance_if_done()
                return self.send(200, {"ok": True})
        self.send(404, {"error": "not found"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    print(f"\n  Players join:  {JOIN_URL}")
    print(f"  Host screen:   http://localhost:{PORT}/host?k={HOSTKEY}\n", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
