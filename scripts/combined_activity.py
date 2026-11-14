#!/usr/bin/env python3
"""Generate a public, aggregate-only GitHub + GitLab contribution calendar.

GitHub: official public contribution calendar via GraphQL (GITHUB_TOKEN).
GitLab: user's own activity events via read_user token (optional).

Only per-day counts are written to the SVGs. No private repository names,
commit subjects, or raw API responses are persisted.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

USER = "misuminitt"
LOCAL_TZ = ZoneInfo("Asia/Jakarta")
OUTPUT = Path(__file__).resolve().parents[1] / "assets"


def get_json(url: str, token: str, payload: dict | None = None):
    headers = {"Accept": "application/json", "User-Agent": "misuminitt-profile-calendar/1.0"}
    if "github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    else:
        headers["PRIVATE-TOKEN"] = token
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, headers=headers, data=data)
    try:
        with urlopen(req, timeout=35) as response:
            result = json.load(response)
            next_page = response.headers.get("X-Next-Page", "")
            return result, next_page
    except HTTPError as exc:
        raise RuntimeError(f"{url.split('/')[2]} API returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"{url.split('/')[2]} API unavailable") from exc


def github_calendar(token: str, start: date, today: date) -> Counter:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for GitHub public contributions")
    start_dt = datetime.combine(start, time.min, LOCAL_TZ).astimezone(timezone.utc)
    end_dt = datetime.now(timezone.utc)
    query = """query($login:String!, $from:DateTime!, $to:DateTime!) {
      user(login:$login) {
        contributionsCollection(from:$from, to:$to) {
          contributionCalendar {
            weeks { contributionDays { date contributionCount } }
          }
        }
      }
    }"""
    payload = {
        "query": query,
        "variables": {
            "login": USER,
            "from": start_dt.isoformat().replace("+00:00", "Z"),
            "to": end_dt.isoformat().replace("+00:00", "Z"),
        },
    }
    result, _ = get_json("https://api.github.com/graphql", token, payload)
    if result.get("errors") or not result.get("data", {}).get("user"):
        raise RuntimeError("GitHub GraphQL contributionCalendar query failed")
    weeks = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    counts = Counter()
    for week in weeks:
        for day in week["contributionDays"]:
            day_date = date.fromisoformat(day["date"])
            if start <= day_date <= today:
                counts[day_date] += int(day["contributionCount"])
    return counts


def gitlab_calendar(token: str, start: date, today: date) -> Counter | None:
    if not token:
        print("GitLab read_user token not configured; rendering GitHub-only activity")
        return None
    counts = Counter()
    page = 1
    seen = set()
    # The API's after filter is date-based; subtract one day to avoid excluding
    # activity on the first day due to exclusive comparison.
    after = (start - timedelta(days=1)).isoformat()
    while page <= 100:
        params = urlencode({"after": after, "per_page": 100, "page": page, "sort": "desc"})
        url = f"https://gitlab.com/api/v4/users/{USER}/events?{params}"
        events, next_page = get_json(url, token)
        if not isinstance(events, list):
            raise RuntimeError("Unexpected GitLab events response")
        for event in events:
            if event.get("imported"):
                continue
            event_id = event.get("id")
            if event_id in seen:
                continue
            seen.add(event_id)
            timestamp = event.get("created_at")
            if not timestamp:
                continue
            day = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(LOCAL_TZ).date()
            if start <= day <= today:
                counts[day] += 1
        if not next_page:
            return counts
        page = int(next_page)
    raise RuntimeError("GitLab returned more than 100 pages; refusing partial counts")


def level(count: int) -> int:
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count <= 3:
        return 2
    if count <= 7:
        return 3
    return 4


def svg(start: date, today: date, gh: Counter, gl: Counter | None, dark: bool) -> str:
    bg = "#0d1117" if dark else "#ffffff"
    fg = "#e6edf3" if dark else "#1f2328"
    muted = "#8b949e" if dark else "#57606a"
    border = "#30363d" if dark else "#d1d9e0"
    empty = "#21262d" if dark else "#ebedf0"
    green = [empty, "#0e4429", "#006d32", "#26a641", "#39d353"] if dark else [empty, "#9be9a8", "#40c463", "#30a14e", "#216e39"]
    orange = [empty, "#713e14", "#a84d13", "#e36d26", "#fca326"] if dark else [empty, "#ffdbb5", "#f8ab61", "#fc6d26", "#b94a00"]
    rect_size, step, top, left = 10, 13, 87, 47
    first_sunday = start - timedelta(days=(start.weekday() + 1) % 7)
    weeks = math.ceil(((today - first_sunday).days + 1) / 7)
    width = max(778, left + weeks * step + 38)
    total_gh = sum(gh.values())
    total_gl = sum(gl.values()) if gl is not None else None
    note = f"{total_gl:,} GitLab events" if gl is not None else "GitLab not connected"
    elems = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="221" viewBox="0 0 {width} 221" role="img" aria-label="GitHub and GitLab daily activity">',
        f'<title>Contribution activity for {USER}: GitHub and GitLab</title>',
        f'<rect x="0.5" y="0.5" width="{width-1}" height="220" rx="12" fill="{bg}" stroke="{border}"/>',
        f'<text x="20" y="29" fill="{fg}" font-family="system-ui,-apple-system,sans-serif" font-size="16" font-weight="600">Contributions across platforms</text>',
        f'<text x="20" y="49" fill="{muted}" font-family="system-ui,-apple-system,sans-serif" font-size="11">{total_gh:,} GitHub contributions  ·  {escape(note)}</text>',
    ]
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    labeled_months = set()
    for col in range(weeks):
        week_start = first_sunday + timedelta(days=7 * col)
        if week_start.year == today.year and week_start.month == today.month and week_start.day > today.day:
            break
        if (week_start.year, week_start.month) not in labeled_months and week_start + timedelta(days=6) >= start:
            labeled_months.add((week_start.year, week_start.month))
            elems.append(f'<text x="{left+col*step}" y="73" fill="{muted}" font-family="system-ui,sans-serif" font-size="10">{month_names[week_start.month - 1]}</text>')
    for weekday, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        elems.append(f'<text x="13" y="{top + weekday*step + 9}" fill="{muted}" font-family="system-ui,sans-serif" font-size="10">{label}</text>')
    for col in range(weeks):
        for weekday in range(7):
            day = first_sunday + timedelta(days=col*7 + weekday)
            if day < start or day > today:
                continue
            g, l = gh.get(day, 0), 0 if gl is None else gl.get(day, 0)
            x, y = left + col*step, top + weekday*step
            elems.append(f'<g><title>{day.isoformat()}: {g} GitHub contributions; {l} GitLab events</title>')
            elems.append(f'<rect x="{x}" y="{y}" width="{rect_size}" height="{rect_size}" rx="2" fill="{green[level(g)] if g else orange[level(l)] if l else empty}"/>')
            if g and l:
                elems.append(f'<rect x="{x+5}" y="{y}" width="5" height="{rect_size}" rx="1" fill="{orange[level(l)]}"/>')
            elems.append('</g>')
    elems.extend([
        f'<rect x="20" y="198" width="10" height="10" rx="2" fill="{green[3]}"/>',
        f'<text x="36" y="207" fill="{muted}" font-family="system-ui,sans-serif" font-size="11">GitHub</text>',
        f'<rect x="119" y="198" width="10" height="10" rx="2" fill="{orange[3]}"/>',
        f'<text x="135" y="207" fill="{muted}" font-family="system-ui,sans-serif" font-size="11">GitLab</text>',
        f'<text x="{width-20}" y="207" text-anchor="end" fill="{muted}" font-family="system-ui,sans-serif" font-size="10">Updated {today.isoformat()} · Asia/Jakarta</text>',
        '</svg>',
    ])
    return "\n".join(elems) + "\n"


def main():
    today = datetime.now(LOCAL_TZ).date()
    start = today - timedelta(days=364)
    gh = github_calendar(os.environ.get("GITHUB_TOKEN", ""), start, today)
    gl = gitlab_calendar(os.environ.get("GITLAB_READ_USER_TOKEN", ""), start, today)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for dark in (True, False):
        (OUTPUT / ("contributions-dark.svg" if dark else "contributions-light.svg")).write_text(
            svg(start, today, gh, gl, dark), encoding="utf-8"
        )
    print(f"Calendar generated: GitHub={sum(gh.values())}, GitLab={'not linked' if gl is None else sum(gl.values())}")


if __name__ == "__main__":
    main()