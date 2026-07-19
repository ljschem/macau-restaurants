"""Regenerate index.html from the crawler DB (macau.db).

Single source of truth = ``macau_restaurant/data/macau.db``. This script reuses the
crawler's own presentation mapping (``macau_crawler.export._build_rows``) so the site
row set matches ``filtered_restaurants.csv`` exactly (rating >= 4.0, reviews >= 100),
then maps those Korean-keyed rows onto the card's ``DATA`` fields and injects them into
``index.template.html`` -> ``index.html``.

Run with the crawler's venv Python (it has pandas/pyyaml/python-dotenv)::

    C:\\Users\\LEE\\Documents\\vibe_code\\macau_restaurant\\.venv\\Scripts\\python.exe build_site.py

No Google API key is needed: _build_rows only reads the DB (+ cached FX rates).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRAWLER_DIR = (HERE.parent / "macau_restaurant").resolve()
DB_PATH = CRAWLER_DIR / "data" / "macau.db"
TEMPLATE = HERE / "index.template.html"
OUTPUT = HERE / "index.html"

# Make the crawler package importable (reuse its proven derivations).
sys.path.insert(0, str(CRAWLER_DIR))

from macau_crawler.storage import Storage        # noqa: E402
from macau_crawler.export import _build_rows      # noqa: E402
from macau_crawler.fx import FxConverter          # noqa: E402

# Matches macau_crawler.config.Config defaults; FX comes from the DB cache, this is fallback only.
_FX_FALLBACK = {"MOP": 168.0, "HKD": 173.0, "USD": 1350.0, "CNY": 188.0}

# 노포·명소 큐레이션은 이제 DB가 단일 소스다: places_enriched.curated_category
# ("명소" / "로컬인기")를 _build_rows가 "큐레이션" 키로 넘겨준다. 값이 있으면 카드에 뱃지가
# 붙고 '노포·명소만' 필터(명소+로컬인기 통합)에 잡힌다. 예전 build_site 하드코딩 목록
# (_CLASSIC_PLACE_IDS)은 DB로 병합되었다 — storage.mark_curated 참고.
_CURATED_CATEGORIES = {"명소", "로컬인기"}


def _menu_list(raw: str) -> list[str]:
    """대표메뉴 is a ' · '-joined string in the export; split back to an array for the card."""
    if not raw:
        return []
    return [m.strip() for m in raw.split(" · ") if m.strip()]


def _clean_price(raw: str) -> str:
    """Normalize a researched lunch price into a clean display string.

    Source values are inconsistent: '24864', '16,464원 (1인 기준)', '13,440~20,160원',
    '약 87,500원 (MOP 480 기준)'. Strip any parenthetical qualifier and, when the value is
    bare digits (no '원'), insert thousands separators and append 원.
    """
    if not raw:
        return ""
    s = re.sub(r"\s*\([^)]*\)", "", str(raw)).strip()
    if not s:
        return ""
    if "원" not in s:
        s = re.sub(r"\d+", lambda m: f"{int(m.group()):,}", s) + "원"
    return s


def _michelin_short(raw: str) -> str:
    """'3스타 (2026)' -> '3스타' for a compact badge; '' when no Michelin listing."""
    if not raw:
        return ""
    return raw.split(" (")[0].strip()


def _to_card(r: dict) -> dict:
    """Map one _build_rows() row (Korean keys) onto the card's DATA field names."""
    try:
        rating = float(r.get("별점") or 0)
    except (TypeError, ValueError):
        rating = 0.0
    try:
        reviews = int(r.get("리뷰수") or 0)
    except (TypeError, ValueError):
        reviews = 0
    return {
        "name_ko": (r.get("이름(한글)") or r.get("상호") or "").strip(),
        "name_en": (r.get("상호") or "").strip(),
        "category": (r.get("카테고리(요리)") or "기타").strip(),
        "price": _clean_price(r.get("가격(1인기준,원화)") or ""),          # 저녁/일반 (1인 기준)
        "price_lunch": _clean_price(r.get("런치가격(1인기준,원화)") or ""),  # 점심 (1인 세트 기준)
        "priceSym": (r.get("가격기호") or "").strip(),
        "rating": rating,
        "reviews": reviews,
        "menu": _menu_list(r.get("대표메뉴") or ""),
        "location": (r.get("지역") or "").strip(),
        "address_hint": (r.get("위치") or "").strip(),
        "pros": (r.get("장점") or "").strip(),
        "intro": (r.get("소개") or "").strip(),
        "michelin": _michelin_short(r.get("미쉐린") or ""),   # 폴리시2: 별도 미쉐린 뱃지
        "map": (r.get("지도") or "").strip(),                  # 폴리시3: 실제 지도 링크
        "curated_category": (r.get("큐레이션") or "").strip(),  # "명소" / "로컬인기" / ""
        "classic": (r.get("큐레이션") or "").strip() in _CURATED_CATEGORIES,  # 🏛️ 큐레이션(명소+로컬인기) 필터
        "hours": (r.get("운영시간") or "").strip(),            # 요일별 영업시간(줄바꿈 구분)
        "breakfast": (r.get("아침가능") or "").strip(),         # '가능' / '불가' / ''
        "id": (r.get("place_id") or "").strip(),               # 즐겨찾기/지도 핀의 안정적 키
        "lat": r.get("위도"),                                  # 위도 (지도 핀)
        "lng": r.get("경도"),                                  # 경도 (지도 핀)
    }


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    if not TEMPLATE.exists():
        raise SystemExit(f"Template not found: {TEMPLATE}")

    storage = Storage(str(DB_PATH))
    try:
        fx = FxConverter(storage, base="USD", fallback=_FX_FALLBACK)
        rows = _build_rows(storage, fx)
    finally:
        storage.close()

    cards = [_to_card(r) for r in rows]
    data_js = json.dumps(cards, ensure_ascii=False, separators=(",", ":"))

    html = TEMPLATE.read_text(encoding="utf-8")
    if "/*__DATA__*/[]" not in html or "__COUNT__" not in html:
        raise SystemExit("Template markers (/*__DATA__*/[] or __COUNT__) missing.")
    html = html.replace("/*__DATA__*/[]", data_js)
    html = html.replace("__COUNT__", str(len(cards)))

    OUTPUT.write_text(html, encoding="utf-8", newline="\n")

    with_michelin = sum(1 for c in cards if c["michelin"])
    with_price = sum(1 for c in cards if c["price"])
    with_lunch = sum(1 for c in cards if c["price_lunch"])
    with_classic = sum(1 for c in cards if c["classic"])
    n_myeongso = sum(1 for c in cards if c["curated_category"] == "명소")
    n_local = sum(1 for c in cards if c["curated_category"] == "로컬인기")
    with_breakfast = sum(1 for c in cards if c["breakfast"] == "가능")
    with_hours = sum(1 for c in cards if c["hours"])
    print(f"Wrote {OUTPUT} - {len(cards)} restaurants "
          f"({with_michelin} michelin, {with_classic} curated [{n_myeongso} 명소 + {n_local} 로컬인기], "
          f"{with_price} with dinner price, {with_lunch} with lunch price, "
          f"{with_breakfast} breakfast-ok, {with_hours} with hours).")


if __name__ == "__main__":
    main()
