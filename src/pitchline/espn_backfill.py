"""Resumable ESPN backfill: scoreboard -> match summary -> parsed shots (raw JSON is not kept).

Stored per match (small): espn/matches/{league}/{event}.json containing the match header, boxscore
totals and the parsed shots, plus a coverage flag saying whether parsed shots reconcile with ESPN's
own boxscore totals. Unofficial endpoints: keep the pace polite.

Run: python -m pitchline.espn_backfill --league eng.1 --from 2023-08-01 --to 2024-06-01
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

from .espn_shots import classify_type, entry_play_id, parse_shots
from .http import HttpStatus, get_json
from .storage import Store, get_store

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
log = logging.getLogger("pitchline.espn")


def _daterange(a: date, b: date, step: int):
    d = a
    while d <= b:
        yield d, min(d + timedelta(days=step - 1), b)
        d += timedelta(days=step)


def list_events(league: str, start: date, end: date, window: int, fetch, sleep) -> list[dict]:
    """Completed events in [start, end]. Falls back to single days if ESPN rejects the window."""
    events: dict[str, dict] = {}

    def pull(a: date, b: date):
        qs = a.strftime("%Y%m%d") if a == b else f"{a:%Y%m%d}-{b:%Y%m%d}"
        data, _ = fetch(f"{BASE}/{league}/scoreboard?dates={qs}&limit=300")
        for e in data.get("events", []):
            if e.get("status", {}).get("type", {}).get("completed"):
                events[e["id"]] = e

    for a, b in _daterange(start, end, window):
        try:
            pull(a, b)
        except HttpStatus as err:
            if err.status != 400 or a == b:
                raise
            log.warning("window %s..%s rejected; using single days", a, b)
            for d, _ in _daterange(a, b, 1):
                pull(d, d)
                sleep(1)
        sleep(1)
    return list(events.values())


def _totals(summary: dict) -> dict[str, dict]:
    out = {}
    for t in summary.get("boxscore", {}).get("teams", []):
        st = {s["name"]: s.get("displayValue") for s in t.get("statistics", [])}
        keys = ("totalShots", "shotsOnTarget", "blockedShots")
        out[t["team"]["displayName"]] = {k: st.get(k) for k in keys}
    return out


SCHEMA = 2   # 2 = player rows carry athlete ids; match files carry a lineup with minutes played

_MINUTE = re.compile(r"(\d+)'(?:\s*\+\s*(\d+)')?")


def parse_minute(text: str | None) -> int | None:
    """"75'" -> 75, "45'+5'" -> 50."""
    m = _MINUTE.search(text or "")
    return int(m.group(1)) + int(m.group(2) or 0) if m else None


def match_length(summary: dict) -> int:
    """Minutes played in the match, stoppage time included (the largest clock shown in the commentary)."""
    ends = [parse_minute(((e.get("play") or {}).get("clock") or {}).get("displayValue"))
            for e in summary.get("commentary", [])]
    return max([90] + [m for m in ends if m is not None])


def _lineup(summary: dict) -> list[dict]:
    """Everyone who played, with minutes. Minutes come from each player's own ESPN play list (substitution
    and red-card clocks), so no name matching is needed. Stoppage time counts as real minutes."""
    end = match_length(summary)
    out = []
    for r in summary.get("rosters", []):
        team = (r.get("team") or {}).get("displayName")
        for pl in r.get("roster", []):
            if not (pl.get("starter") or pl.get("subbedIn")):
                continue
            plays = pl.get("plays") or []
            subs = sorted(m for m in (parse_minute((p.get("clock") or {}).get("displayValue"))
                                      for p in plays if p.get("substitution")) if m is not None)
            reds = [m for m in (parse_minute((p.get("clock") or {}).get("displayValue"))
                                for p in plays if p.get("redCard")) if m is not None]
            start = 0
            if not pl.get("starter"):
                start = subs.pop(0) if subs else None
            stop = end
            if pl.get("subbedOut") and subs:
                stop = subs[-1]
            if reds:
                stop = min(stop, reds[0])
            ath = pl.get("athlete") or {}
            out.append({"team": team, "athlete_id": str(ath.get("id") or ""),
                        "player": ath.get("displayName"),
                        "starter": bool(pl.get("starter")),
                        "minutes": None if start is None else max(0, stop - start)})
    return out


def _players(summary: dict) -> list[dict]:
    """ESPN's own per-player match stats (authoritative for shots / shots on target / goals / assists)."""
    out = []
    for r in summary.get("rosters", []):
        team = (r.get("team") or {}).get("displayName")
        for pl in r.get("roster", []):
            st = {s["name"]: s.get("value") for s in pl.get("stats", [])}
            row = {"team": team, "player": (pl.get("athlete") or {}).get("displayName"),
                   "athlete_id": str((pl.get("athlete") or {}).get("id") or ""),
                   "shots": int(st.get("totalShots") or 0),
                   "shots_on_target": int(st.get("shotsOnTarget") or 0),
                   "goals": int(st.get("totalGoals") or 0), "assists": int(st.get("goalAssists") or 0),
                   "own_goals": int(st.get("ownGoals") or 0)}
            if any(row[k] for k in ("shots", "shots_on_target", "goals", "assists", "own_goals")):
                out.append(row)
    return out


def boxscore_usable(totals: dict[str, dict]) -> bool:
    """ESPN's boxscore is empty for some older matches (every statistic reads 0). Two teams never both have
    zero shots, so an all-zero boxscore is missing data, not a result."""
    def n(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return bool(totals) and any(n(v["totalShots"]) > 0 for v in totals.values())


def reconcile(shots: list[dict], totals: dict[str, dict], detail: list | None = None,
              players: list[dict] | None = None) -> str:
    """Strict check on TOTAL shots per team. On-target is deliberately not part of the check:
    ESPN counts some blocked shots as on target in its own stats while commentary only says 'blocked'.

    If the boxscore is empty but ESPN's per-player stats exist, the check falls back to those: the sum of the
    team's player shots plus its saved or missed penalties (ESPN's player totalShots leaves those out)."""
    if not totals or all(v["totalShots"] is None for v in totals.values()):
        return "no_boxscore"
    if not shots:
        return "no_commentary"
    status = "ok"
    teams_in_shots = sorted({s["team"] for s in shots if s["team"]})
    use_roster = not boxscore_usable(totals)
    if use_roster and not players:
        return "no_boxscore"
    for team, tot in totals.items():
        mine = [s for s in shots if s["team"] == team and s["outcome"] != "own_goal"]
        if use_roster:
            # ESPN's player totalShots counts a scored penalty (it is a goal) but not a saved or missed one.
            expected = (sum(p["shots"] for p in players if p["team"] == team)
                        + sum(1 for s in mine if s["situation"] == "penalty" and s["outcome"] != "goal"))
            source = "player stats"
        else:
            expected, source = tot["totalShots"], "boxscore"
        if str(len(mine)) != str(expected):
            status = "mismatch"
            if detail is not None:
                detail.append(f"{team}: parsed total={len(mine)} {source} total={expected}"
                              f" shot-teams={teams_in_shots}")
            if detail is not None and len(mine) > int(expected or 0):
                for sh in mine[:8]:
                    detail.append(f"  [{team}] {sh['seconds']}s {sh['outcome']} {sh['player']} "
                                  f"(play {sh['play_id']}) {sh['raw_text'][:60]!r}")
    return status


def on_target_gap(shots: list[dict], totals: dict[str, dict]) -> int:
    """Boxscore on-target minus commentary on-target, summed over teams (informational)."""
    gap = 0
    for team, tot in totals.items():
        mine = [s for s in shots if s["team"] == team and s["outcome"] != "own_goal"]
        try:
            gap += int(tot["shotsOnTarget"]) - sum(1 for s in mine if s["on_target"])
        except (TypeError, ValueError):
            pass
    return gap


# Prefixes of commentary lines that describe a shot. Fouls, VAR and handball lines that merely mention
# a penalty are deliberately not matched, to keep the diagnostics readable.
_SHOTLIKE = ("attempt", "goal!", "penalty saved", "penalty missed", "own goal")
_STOPPAGE_SECONDS = (2700.0, 5400.0, 6300.0, 7200.0)
_WOODWORK = re.compile(r"\bhits? the .*\b(post|bar|woodwork)\b")


def suspicious_lines(summary: dict, shots: list[dict]) -> list[str]:
    """Lines that look like shots but were not parsed, plus possible duplicate shots (diagnostics)."""
    parsed = {s["play_id"] for s in shots}
    out = []
    for e in summary.get("commentary", []):
        p = e.get("play") or {}
        text = e.get("text") or ""
        pid = entry_play_id(e)
        if pid not in parsed and classify_type((p.get("type") or {}).get("text")) is not None:
            ptype = (p.get("type") or {}).get("text")
            out.append(f"INCONSISTENT play typed {ptype!r} but text={text[:120]!r}")
        elif pid not in parsed and (text.lower().startswith(_SHOTLIKE) or _WOODWORK.search(text.lower())):
            out.append(f"UNPARSED type={(p.get('type') or {}).get('text')!r} text={text[:150]!r}")
    seen: dict[tuple, int] = {}
    for sh in shots:
        k = (sh["team"], sh["player"], sh["seconds"], sh["outcome"])
        seen[k] = seen.get(k, 0) + 1
    # ESPN pins stoppage-time events to the end-of-half clock value, so repeats there can be real shots.
    out += [f"DUPLICATE? {k} x{n}" for k, n in seen.items() if n > 1 and k[2] not in _STOPPAGE_SECONDS]
    return out


_SUMMARY_LINE = re.compile(r"\) (?:Shot|Penalty)\b.* at \d+'\s*$")


def drop_duplicate_shots(shots: list[dict], totals: dict[str, dict]) -> tuple[list[dict], int]:
    """ESPN sometimes lists one shot twice: as a long commentary line and again as a one-line summary
    ("Name (Team) Shot Blocked at 20'") or as a plain-text twin of a structured play. When a team has MORE
    parsed shots than ESPN's own total, drop that many likely duplicates, but only if the team then matches
    ESPN's total exactly. If it would not match, nothing is dropped: the match stays flagged."""
    if not boxscore_usable(totals):
        return shots, 0
    drop: set[int] = set()
    for team, tot in totals.items():
        try:
            expected = int(tot["totalShots"])
        except (TypeError, ValueError, KeyError):
            continue
        mine = [i for i, sh in enumerate(shots) if sh["team"] == team and sh["outcome"] != "own_goal"]
        extra = len(mine) - expected
        if extra <= 0:
            continue
        summaries = [i for i in mine if _SUMMARY_LINE.search(shots[i].get("raw_text") or "")]
        twins = [i for i in mine if i not in summaries and str(shots[i]["play_id"]).startswith("text:")
                 and any(j != i and shots[j]["player"] == shots[i]["player"]
                         and abs((shots[j]["seconds"] or 0) - (shots[i]["seconds"] or 0)) <= 120
                         for j in mine)]
        candidates = summaries + twins
        if len(candidates) >= extra:
            drop.update(candidates[:extra])
    if not drop:
        return shots, 0
    kept = [sh for i, sh in enumerate(shots) if i not in drop]
    return (kept, len(drop)) if all(
        _count(kept, t) == _expected(tot) for t, tot in totals.items() if _expected(tot) is not None
        and _count(shots, t) > _expected(tot)) else (shots, 0)


def _expected(tot: dict) -> int | None:
    try:
        return int(tot["totalShots"])
    except (TypeError, ValueError, KeyError):
        return None


def _count(shots: list[dict], team: str) -> int:
    return sum(1 for sh in shots if sh["team"] == team and sh["outcome"] != "own_goal")


def process_event(store: Store, league: str, ev: dict, fetch, retry_bad: bool = False,
                  upgrade: bool = False) -> str | None:
    key = f"espn/matches/{league}/{ev['id']}.json"
    if store.exists(key):
        old = json.loads(store.read_bytes(key))
        wanted = ((retry_bad and old.get("coverage") != "ok")
                  or (upgrade and old.get("schema", 1) < SCHEMA))   # --upgrade: older layout
        if not wanted:
            return None
    summary, _ = fetch(f"{BASE}/{league}/summary?event={ev['id']}")
    shots = parse_shots(summary, ev["id"], league)
    shots, deduped = drop_duplicate_shots(shots, _totals(summary))
    comp = ev["competitions"][0]
    rec = {
        "league": league, "event_id": ev["id"], "date": ev["date"],
        "season": (ev.get("season") or {}).get("year"),
        "teams": [{"name": c["team"]["displayName"], "home": c["homeAway"] == "home",
                   "score": c.get("score")} for c in comp["competitors"]],
        "boxscore_totals": _totals(summary), "shots": shots, "players": _players(summary),
        "lineup": _lineup(summary), "schema": SCHEMA, "duplicates_dropped": deduped,
    }
    rec["on_target_gap"] = on_target_gap(shots, rec["boxscore_totals"])
    detail: list[str] = []
    rec["reconciled_with"] = "boxscore" if boxscore_usable(rec["boxscore_totals"]) else "player_stats"
    rec["coverage"] = reconcile(shots, rec["boxscore_totals"], detail, rec["players"])
    if rec["coverage"] != "ok":
        log.warning("event %s %s: %s | commentary entries=%d, shots parsed=%d | %s", ev["id"],
                    rec["coverage"], " / ".join(t["name"] for t in rec["teams"]),
                    len(summary.get("commentary", [])), len(shots), "; ".join(detail))
        for line in suspicious_lines(summary, shots):
            log.warning("  event %s %s", ev["id"], line)
    store.write_bytes(key, json.dumps(rec).encode())
    return rec["coverage"]


def backfill(store: Store, league: str, start: date, end: date, fetch=None, sleep=time.sleep,
             window: int = 7, max_minutes: float = 300, clock=time.monotonic,
             retry_bad: bool = False, upgrade: bool = False) -> dict:
    fetch = fetch or (lambda url: get_json(url))
    t0 = clock()
    events = list_events(league, start, end, window, fetch, sleep)
    stats = {"listed": len(events), "new": 0, "skipped": 0, "failed": 0, "stopped_early": False}
    for ev in sorted(events, key=lambda e: e["date"]):
        if (clock() - t0) / 60 > max_minutes:
            stats["stopped_early"] = True
            break
        try:
            cov = process_event(store, league, ev, fetch, retry_bad, upgrade)
        except Exception as err:  # one bad match must not stop the run
            log.error("event %s failed: %s", ev["id"], err)
            stats["failed"] += 1
            continue
        if cov is None:
            stats["skipped"] += 1
        else:
            stats["new"] += 1
            stats[cov] = stats.get(cov, 0) + 1
            sleep(1)
    log.info("%s %s..%s %s", league, start, end, stats)
    return stats


def recent_targets(today: date, days: int) -> list[dict]:
    """Every competition the site tracks, for the last few days: the nightly catch-up."""
    from .live import LEAGUES
    return [{"league": lg, "from": (today - timedelta(days=days)).isoformat(), "to": today.isoformat()}
            for lg in LEAGUES]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--targets", help="YAML list of {league, from, to}; processed in order")
    ap.add_argument("--recent", type=int, metavar="DAYS",
                    help="all tracked competitions for the last DAYS days (the nightly catch-up)")
    ap.add_argument("--root")
    ap.add_argument("--window", type=int, default=7)
    ap.add_argument("--retry-bad", action="store_true",
                    help="re-download matches whose stored coverage is not 'ok'")
    ap.add_argument("--upgrade", action="store_true",
                    help="re-download matches stored in an older file layout (adds athlete ids and minutes)")
    ap.add_argument("--max-minutes", type=float, default=300)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parse = lambda s: datetime.strptime(s, "%Y-%m-%d").date()  # noqa: E731
    store = get_store(a.root)
    if a.targets:
        import yaml

        targets = yaml.safe_load(open(a.targets))["targets"]
    elif a.recent:
        targets = recent_targets(datetime.now(timezone.utc).date(), a.recent)
    elif a.league == "all" and a.start and a.end:
        from .live import LEAGUES
        targets = [{"league": lg, "from": a.start, "to": a.end} for lg in LEAGUES]
    elif a.league and a.start and a.end:
        targets = [{"league": a.league, "from": a.start, "to": a.end}]
    else:
        ap.error("give --targets, --recent, or --league with --from and --to")
    t0, bad = time.monotonic(), False
    for t in targets:
        left = a.max_minutes - (time.monotonic() - t0) / 60
        if left <= 0:
            log.info("time budget used up; remaining targets run next time")
            break
        stats = backfill(store, t["league"], parse(str(t["from"])), parse(str(t["to"])),
                         window=a.window, max_minutes=left, retry_bad=a.retry_bad, upgrade=a.upgrade)
        bad = bad or bool(stats["failed"] and not stats["new"])
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
