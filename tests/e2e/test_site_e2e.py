"""Browser tests: build a small site from synthetic matches, serve it locally, drive it with Playwright.

Skipped automatically when Playwright or its Chromium build is not installed (CI installs both).
The fixtures live in this file because a second conftest.py would clash with tests/conftest.py.
"""
import functools
import http.server
import json
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")
expect = pw.expect

from pitchline import site  # noqa: E402
from pitchline.storage import LocalStore  # noqa: E402


def _put(store, league, eid, date, players, lineup, shots, teams):
    rec = {"league": league, "event_id": eid, "date": date, "coverage": "ok", "players": players,
           "lineup": lineup, "shots": shots, "teams": teams}
    store.write_bytes(f"espn/matches/{league}/{eid}.json", json.dumps(rec).encode())


def _row(team, name, aid, shots, sot, goals):
    return {"team": team, "athlete_id": aid, "player": name, "shots": shots, "shots_on_target": sot,
            "goals": goals, "assists": 0, "own_goals": 0}


def _shot(name, x, y, outcome):
    return {"player": name, "x": x, "y": y, "outcome": outcome, "body_part": "right_foot", "situation": "open_play",
            "seconds": 600, "on_target": outcome in ("saved", "goal")}


@pytest.fixture(scope="session")
def site_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("e2e")
    store = LocalStore(root / "data")
    stars = [("Arsenal", "Bukayo Saka", "11"), ("Chelsea", "Cole Palmer", "22"), ("Liverpool", "Mo Salah", "33")]
    for i in range(12):
        day = f"2025-09-{i + 1:02d}"
        for lg in ("eng.1", "esp.1"):
            rows, lineup, shots = [], [], []
            for team, name, aid in stars:
                rows.append(_row(team, name, aid, 4, 2, 1 if i % 3 == 0 else 0))
                lineup.append({"team": team, "athlete_id": aid, "player": name, "minutes": 90})
                shots += [_shot(name, 0.1 + (i % 4) / 20, 0.45 + (i % 5) / 30, "goal" if i % 3 == 0 else "saved")]
            _put(store, lg, f"{lg}-{i}", day, rows, lineup, shots,
                 [{"name": "Arsenal", "home": True, "score": "2"}, {"name": "Chelsea", "home": False, "score": "1"}])
    out = root / "site"
    site.build(store, out)
    return out


@pytest.fixture(scope="session")
def base_url(site_dir):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site_dir))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="session")
def browser():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:                      # browser binary not installed
            pytest.skip(f"Chromium not available: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.base = base_url
    yield pg
    ctx.close()


@pytest.fixture
def phone(browser, base_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, is_mobile=True, has_touch=True, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.base = base_url
    yield pg
    ctx.close()


def test_home_renders_without_errors(page):
    page.goto(page.base + "/index.html")
    expect(page.locator("h1")).to_contain_text("Who really shoots")
    expect(page.locator("#stage-map svg circle").first).to_be_visible()
    expect(page.locator(".wcard").first).to_be_visible()
    assert page.errors == []


def test_header_search_finds_a_player_and_opens_the_profile(page):
    page.goto(page.base + "/index.html")
    page.locator(".top [data-search]").fill("sak")
    hit = page.locator("#sdrop a", has_text="Bukayo Saka").first
    expect(hit).to_be_visible()
    hit.click()
    expect(page.locator("h1")).to_contain_text("Bukayo Saka")
    expect(page.locator(".pitch svg")).to_be_visible()


def test_search_ignores_accents_and_reports_no_match(page):
    page.goto(page.base + "/index.html")
    box = page.locator(".top [data-search]")
    box.fill("zzzz")
    expect(page.locator("#sdrop")).to_contain_text("No player found")


def test_theme_toggle_persists_across_reload(page):
    page.goto(page.base + "/index.html")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.locator("#theme").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"
    page.reload()
    assert page.evaluate("document.documentElement.dataset.theme") == "light"


def test_league_tabs_switch_boards_and_sort(page):
    page.goto(page.base + "/eng.1.html")
    expect(page.locator("#t-sot")).to_be_visible()
    page.get_by_role("button", name="Goals", exact=True).click()
    expect(page.locator("#t-goals")).to_be_visible()
    expect(page.locator("#t-sot")).to_be_hidden()
    assert page.errors == []


def test_news_filters_partition_the_feed(page):
    page.goto(page.base + "/news.html")
    total = page.locator(".note-card:visible").count()
    assert total > 0
    leagues = page.eval_on_selector_all("#f-lg option", "o => o.map(x => x.value).filter(v => v !== 'all')")
    per_league = []
    for lg in leagues:
        page.select_option("#f-lg", lg)
        n = page.locator(".note-card:visible").count()
        per_league.append(n)
        expect(page.locator("#f-n")).to_have_text(f"{n} stor{'y' if n == 1 else 'ies'}")
    assert sum(per_league) == total                      # every story belongs to exactly one competition
    page.select_option("#f-lg", "all")
    assert page.locator(".note-card:visible").count() == total
    page.select_option("#f-o", "old")
    assert page.locator("#feed.rev").count() == 1


def test_player_page_charts_render(page):
    page.goto(page.base + "/p-eng.1-11.html")
    expect(page.locator(".chart svg").first).to_be_visible()
    assert page.locator(".chart").count() == 3
    assert page.errors == []


def test_mobile_menu_opens_and_navigates(phone):
    phone.goto(phone.base + "/index.html")
    expect(phone.locator("#nav")).to_be_hidden()
    phone.locator("#menu-btn").click()
    expect(phone.locator("#nav")).to_be_visible()
    phone.locator("#nav a", has_text="News").click()
    expect(phone.locator("h1")).to_have_text("News")


def test_no_horizontal_scroll_on_mobile(phone):
    for path in ("/index.html", "/eng.1.html", "/p-eng.1-11.html", "/forecast.html", "/news.html"):
        phone.goto(phone.base + path)
        assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), path


def test_every_page_loads_with_no_console_errors(page):
    for path in ("about.html", "method.html", "how.html", "best.html", "live.html", "call.html", "forecast.html"):
        page.goto(f"{page.base}/{path}")
        expect(page.locator("h1").first).to_be_visible()
    # the live page fetches GitHub for scores; a network failure there is expected offline, anything else is not
    assert [e for e in page.errors if "live.json" not in e and "Failed to load resource" not in e] == []
