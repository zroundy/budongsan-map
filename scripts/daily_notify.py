#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
판다 부동산(/unsold)에서 내 조건 매물을 매일 필터해 텔레그램으로 알림.
- 데이터: https://api.pandarealestate.store/api/v1/listings/unsold (공개, GET)
- 상세: /api/v1/listings/{id}
- 도보: Tmap(walk_time). 좌표는 매물에 포함되어 지오코딩 불필요.
- 상태: digest.json(신규 판별 + 날짜별 적재)을 저장. GitHub Actions가 repo에 커밋.

환경변수:
  TMAP_APP_KEY        (필수) 도보 계산
  TELEGRAM_BOT_TOKEN  (없으면 콘솔 출력만)
  TELEGRAM_CHAT_ID
날짜 인자:
  RUN_DATE (YYYY-MM-DD) - 미지정 시 오늘(KST). Actions에서 주입.
"""
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE)
sys.path.insert(0, BASE)
from walk_time import DEFAULT_STATIONS, _haversine_km, nearest_station  # noqa: E402

UNSOLD = "https://api.pandarealestate.store/api/v1/listings/unsold"
DETAIL = "https://api.pandarealestate.store/api/v1/listings/%s"
LISTING_URL = "https://www.pandarealestate.store/listings/%s"
STATE = os.path.join(SKILL_DIR, "digest.json")

DEPOSIT_MAX = 150_000_000   # 전세 1.5억 (원)
MGMT_MAX = 10               # 관리비 10만원
WALK_LIMIT = 15             # 도보 분
WALK_BAND = 2               # 15~17분 재검토
PREFILTER_KM = 1.6          # 역 직선거리 이내만 Tmap 계산(호출 절감)
KEEP_DAYS = 14              # 알림 메시지에 유지할 날짜 수
BUILT_LIMIT_Y = 10          # 준공 10년 이내 선호

ROOM = {"ONE_ROOM": "원룸", "TWO_ROOM": "투룸", "THREE_ROOM": "쓰리룸"}


def http_json(url, method="GET", body=None):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
               "Origin": "https://www.pandarealestate.store"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def eok(won):
    return "%.2f억" % (won / 100_000_000)


def years_since(datestr, run_date):
    try:
        y = int(datestr[:4]); m = int(datestr[5:7])
        ry = int(run_date[:4]); rm = int(run_date[5:7])
        return (ry - y) + (rm - m) / 12.0
    except Exception:
        return None


def passes_list(it):
    if it.get("monthlyRent", 1) != 0:
        return False  # 전세만
    if it.get("deposit", 10 ** 12) > DEPOSIT_MAX:
        return False
    if "HF_YOUTH" not in (it.get("loanProducts") or []):
        return False  # 버팀목
    if it.get("latitude") is None or it.get("longitude") is None:
        return False
    lat, lon = it["latitude"], it["longitude"]
    near = min(_haversine_km(lat, lon, s["lat"], s["lon"]) for s in DEFAULT_STATIONS)
    return near <= PREFILTER_KM


def is_banjiha(detail):
    cf = detail.get("currentFloor")
    if isinstance(cf, (int, float)) and cf <= 0:
        return True
    addr = detail.get("address", "")
    for kw in ("반지하", "반지층", "지층"):
        if kw in addr:
            return True
    return "지하" in addr and "지하철" not in addr


def evaluate(it, key, run_date):
    """리스트 통과분 -> 상세 확인 + 도보. 조건 충족 시 요약 dict, 아니면 None."""
    detail = http_json(DETAIL % it["id"])
    if (detail.get("maintenanceFee") or 0) > MGMT_MAX:
        return None
    if is_banjiha(detail):
        return None
    if detail.get("illegalBuildingStatus") == "YES":
        return None  # 위반건축물(보증보험/대출 불가 가능성)
    coord = (it["latitude"], it["longitude"])
    nb = nearest_station(coord, DEFAULT_STATIONS, key, top_k=2)
    if not nb:
        return None
    wm = round(nb["sec"] / 60, 1)
    if wm > WALK_LIMIT + WALK_BAND:
        return None
    status = "통과" if wm <= WALK_LIMIT else "재검토"
    built = years_since(detail.get("useAprDay", ""), run_date)
    return {
        "id": it["id"],
        "address": it["address"],
        "deposit": eok(it["deposit"]),
        "mgmt": detail.get("maintenanceFee", 0),
        "station": nb["station"],
        "walk_min": wm,
        "room": ROOM.get(it.get("roomType"), it.get("roomType", "-")),
        "status": status,
        "built_over": (built is not None and built > BUILT_LIMIT_Y),
        "built_y": None if built is None else round(built, 1),
        "url": LISTING_URL % it["id"],
    }


def load_state():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return {"seen": {}, "days": {}}


def build_message(days, run_date):
    lines = ["[내 조건 매물] %s 기준" % run_date, ""]
    for date in sorted(days.keys(), reverse=True):
        items = days[date]
        lines.append("■ %s (%d건)" % (date, len(items)))
        for i, m in enumerate(items, 1):
            tag = "[통과]" if m["status"] == "통과" else "[재검토]"
            built = ""
            if m.get("built_over"):
                built = " / 준공%s년(감점)" % m["built_y"]
            lines.append("%d. %s %s" % (i, m["address"], tag))
            lines.append("   전세 %s / 관리비 %s만 / %s 도보 %s분 / %s%s"
                         % (m["deposit"], m["mgmt"], m["station"], m["walk_min"], m["room"], built))
            lines.append("   %s" % m["url"])
        lines.append("")
    msg = "\n".join(lines).strip()
    if len(msg) > 3800:
        msg = msg[:3780] + "\n… (이하 생략)"
    return msg


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("[텔레그램 미설정] 아래 메시지를 콘솔 출력합니다.\n")
        print(text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    try:
        http_json(url, method="POST", body=body)
        print("텔레그램 발송 완료.")
    except Exception as e:
        print("텔레그램 발송 실패:", e)
        print(text)


def main():
    key = os.environ.get("TMAP_APP_KEY")
    if not key:
        sys.exit("[오류] TMAP_APP_KEY 환경변수가 필요합니다.")
    run_date = os.environ.get("RUN_DATE") or "오늘"

    data = http_json(UNSOLD)
    items = data if isinstance(data, list) else next(
        (v for v in data.values() if isinstance(v, list)), [])
    print("전체 매물:", len(items))

    state = load_state()
    seen = state.get("seen", {})
    days = state.get("days", {})

    candidates = [it for it in items if passes_list(it)]
    print("리스트 1차 통과(전세/보증금/버팀목/역근처):", len(candidates))

    new_matches = []
    for it in candidates:
        if str(it["id"]) in seen:
            continue  # 이미 본 매물
        try:
            m = evaluate(it, key, run_date)
        except Exception as e:
            print("상세/도보 실패 id=%s: %s" % (it["id"], e))
            continue
        seen[str(it["id"])] = run_date
        if m:
            new_matches.append(m)
    print("신규 조건 충족:", len(new_matches))

    if new_matches:
        new_matches.sort(key=lambda x: (0 if x["status"] == "통과" else 1, x["walk_min"]))
        days.setdefault(run_date, [])
        days[run_date] = new_matches + days[run_date]

    # 오래된 날짜 정리
    for d in sorted(days.keys(), reverse=True)[KEEP_DAYS:]:
        days.pop(d, None)

    state["seen"] = seen
    state["days"] = days
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    if not days:
        print("표시할 매물이 없습니다.")
        return
    msg = build_message(days, run_date)
    # 신규가 없으면 조용히(중복 알림 방지): 신규 0건이면 발송 생략 옵션
    if not new_matches and os.environ.get("NOTIFY_ALWAYS") != "1":
        print("신규 없음 - 발송 생략(전체 현황은 digest에 유지).")
        return
    send_telegram(msg)


if __name__ == "__main__":
    main()
