"""Understat에서 EPL·라리가 시즌 데이터를 내려받아 data/understat_raw.json 으로 저장.

표준 라이브러리만 사용한다 (pip install 불필요).
엔드포인트: https://understat.com/getLeagueData/{league}/{season}
  league  : EPL | La_liga | Serie_A | Bundesliga | Ligue_1 | RFPL
  season  : 시즌 시작 연도. 2025 = 2025/26

반환 JSON = {"teams": {...}, "players": [...], "dates": [...]}
"""
import gzip, json, os, sys, time, urllib.request, urllib.error
from datetime import date

LEAGUES = ["EPL", "La_liga"]
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "understat_raw.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def current_season(today=None):
    """유럽 시즌은 7월에 시작한다. 2026년 9월 -> 2026 (=2026/27)."""
    t = today or date.today()
    return t.year if t.month >= 7 else t.year - 1


def get(url, tries=4):
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://understat.com/",
        })
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b" or r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except Exception as e:                     # noqa: BLE001
            last = e
            wait = 3 * (i + 1)
            print(f"  재시도 {i+1}/{tries} ({e}) — {wait}s 대기", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"{url} 실패: {last}")


def main():
    cur = current_season()
    seasons = [cur - 2, cur - 1, cur]
    out = {}
    for lg in LEAGUES:
        for yr in seasons:
            key = f"{lg}_{yr}"
            d = get(f"https://understat.com/getLeagueData/{lg}/{yr}")
            players = d.get("players") or []
            teams = d.get("teams") or {}
            played = max((len(t.get("history", [])) for t in teams.values()), default=0)
            print(f"{key}: 선수 {len(players)}명 · 팀 {len(teams)}개 · 최다 {played}경기")
            if not teams:
                print(f"  ! {key} 비어 있음 — 아직 시작 전인 시즌일 수 있음")
            out[key] = d
            time.sleep(1.2)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n저장: {os.path.abspath(OUT)} ({os.path.getsize(OUT)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
