// Know-It-All Live: game rules. Runs inside the host's browser tab (and under node for tests).
// The host is the only authority; phones just send join / answer / hazel-done messages.
(function (root) {
  const ACTIVE_MS = 15000;               // a phone silent this long stops blocking the game
  const FAST_S = 30, MAX_PTS = 1000, MIN_PTS = 500;

  function shuffle(a) {
    a = a.slice();
    for (let k = a.length - 1; k > 0; k--) { const j = Math.floor(Math.random() * (k + 1)); [a[k], a[j]] = [a[j], a[k]]; }
    return a;
  }

  class Game {
    constructor(questions, saved) {
      this.Q = questions;
      Object.assign(this, { phase: "lobby", qi: -1, order: [], qstart: 0, players: {}, kicked: [] }, saved || {});
    }
    snapshot() {
      const { phase, qi, order, qstart, players, kicked } = this;
      return { phase, qi, order, qstart, players, kicked };
    }
    reset() {
      Object.assign(this, { phase: "lobby", qi: -1, order: [], qstart: 0 });
      for (const p of Object.values(this.players)) Object.assign(p, { score: 0, correct: 0, answers: {}, hazel: false, late: null });
    }
    active(p, now) { return now - p.seen < ACTIVE_MS; }
    waitingOn(now) {
      const ps = Object.values(this.players).filter(p => this.active(p, now));
      if (this.phase === "question") return ps.filter(p => !(this.qi in p.answers) && p.late !== this.qi);
      if (this.phase === "hazel") return ps.filter(p => !p.hazel);
      return [];
    }
    advance(now) {
      if (this.waitingOn(now).length) return;
      const ps = Object.values(this.players);
      if (this.phase === "question" && ps.some(p => this.qi in p.answers)) this.phase = "reveal";
      else if (this.phase === "hazel" && ps.some(p => p.hazel)) this.phase = "final";
    }

    // "hi" doubles as join and keep-alive, so a phone that reloads or reconnects just carries on
    hi(pid, name, now) {
      if (!pid || this.kicked.includes(pid)) return;
      const p = this.players[pid];
      if (p) { p.seen = now; return; }
      name = String(name || "").split(/\s+/).join(" ").trim().slice(0, 16) || "Player";
      const taken = new Set(Object.values(this.players).map(p => p.name.toLowerCase()));
      let base = name, n = 2;
      while (taken.has(name.toLowerCase())) name = `${base.slice(0, 13)} ${n++}`;
      this.players[pid] = { name, score: 0, correct: 0, answers: {}, hazel: false, seen: now,
                            late: this.phase === "question" ? this.qi : null };
    }
    kick(pid) { delete this.players[pid]; this.kicked.push(pid); }
    start(now) {
      if (!Object.keys(this.players).length) return;
      this.reset();
      this.order = shuffle(this.Q).map(q => {
        const c = shuffle([q.a, ...q.w]);
        return { s: q.s, q: q.q, c, a: c.indexOf(q.a), x: q.x };
      });
      Object.assign(this, { qi: 0, phase: "question", qstart: now });
    }
    answer(pid, k, qi, now) {
      const p = this.players[pid];
      if (!p || this.phase !== "question" || qi !== this.qi || this.qi in p.answers || !(k >= 0 && k < 4)) return;
      p.seen = now;
      const t = (now - this.qstart) / 1000, ok = k === this.order[this.qi].a;
      const pts = ok ? Math.round(MAX_PTS - (MAX_PTS - MIN_PTS) * Math.min(t, FAST_S) / FAST_S) : 0;
      p.answers[this.qi] = { k, ok, pts };
      p.score += pts; p.correct += ok ? 1 : 0;
      this.advance(now);
    }
    hazel(pid, now) {                      // only "done" ever arrives; the typed answer never leaves the phone
      const p = this.players[pid];
      if (!p || this.phase !== "hazel") return;
      p.hazel = true; p.seen = now;
      this.advance(now);
    }
    next(now) {
      if (this.phase === "question") this.phase = "reveal";
      else if (this.phase === "reveal") {
        if (this.qi + 1 < this.order.length) Object.assign(this, { qi: this.qi + 1, phase: "question", qstart: now });
        else this.phase = "hazel";
      } else if (this.phase === "hazel") this.phase = "final";
    }

    view(now) {
      this.advance(now);
      const waiting = new Set(this.waitingOn(now));
      const ranked = Object.entries(this.players).sort(([, a], [, b]) =>
        b.score - a.score || b.correct - a.correct || a.name.localeCompare(b.name));
      const players = ranked.map(([id, p], i) => {
        const a = this.qi >= 0 ? p.answers[this.qi] : null;
        const row = { id, name: p.name, score: p.score, correct: p.correct, rank: i + 1,
                      active: this.active(p, now), waiting: waiting.has(p),
                      answered: !!a, late: this.phase === "question" && p.late === this.qi, hazel: p.hazel };
        if (this.phase === "reveal") Object.assign(row, { pick: a ? a.k : null, ok: !!(a && a.ok), pts: a ? a.pts : 0 });
        return row;
      });
      const v = { phase: this.phase, qi: this.qi, n: this.order.length || this.Q.length, players, kicked: this.kicked };
      if (this.phase === "question" || this.phase === "reveal") {
        const q = this.order[this.qi];
        v.q = { s: q.s, q: q.q, c: q.c, elapsed: Math.floor((now - this.qstart) / 1000) };
        if (this.phase === "reveal") Object.assign(v.q, { a: q.a, x: q.x });   // the answer is only sent once revealed
      }
      return v;
    }
  }

  if (typeof module !== "undefined") module.exports = { Game };
  else root.KIAGame = Game;
})(this);
