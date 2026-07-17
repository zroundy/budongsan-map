#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
매물 지번 주소 -> 최근접 2호선 역(사당~신림)까지 도보 경로 시간 자동 계산.
Tmap Open API 단독 사용: 통합 지오코딩(fullAddrGeo) + 보행자 경로안내(routes/pedestrian).

사용법:
    python walk_time.py "서울 관악구 봉천동 673-88" "서울 관악구 신림동 1619-30"
  또는 파일(한 줄에 주소 하나):
    python walk_time.py --file addresses.txt

설정: 같은 폴더의 config.json (없으면 config.example.json 참고).
      환경변수 TMAP_APP_KEY 가 있으면 그것을 우선 사용.
표준 라이브러리만 사용(설치 불필요).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(os.path.dirname(BASE), "config.json")

GEO_URL = "https://apis.openapi.sk.com/tmap/geo/fullAddrGeo"
PED_URL = "https://apis.openapi.sk.com/tmap/routes/pedestrian"


DEFAULT_STATIONS = [
    {"name": "사당역", "lat": 37.47653, "lon": 126.98166},
    {"name": "낙성대역", "lat": 37.47698, "lon": 126.96368},
    {"name": "서울대입구역", "lat": 37.48132, "lon": 126.95269},
    {"name": "봉천역", "lat": 37.48240, "lon": 126.94180},
    {"name": "신림역", "lat": 37.48425, "lon": 126.92973},
]


def load_config():
    """설정 로드. 배포 환경에선 config.json 없이 환경변수 TMAP_APP_KEY 만으로 동작."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    key = os.environ.get("TMAP_APP_KEY") or cfg.get("tmap_app_key", "")
    if not key or key.startswith("여기에"):
        sys.exit("[오류] Tmap appKey가 없습니다. 환경변수 TMAP_APP_KEY 또는 config.json 을 설정하세요.")
    cfg["tmap_app_key"] = key
    cfg.setdefault("walk_limit_min", int(os.environ.get("WALK_LIMIT_MIN", 15)))
    cfg.setdefault("review_band_min", 2)
    if not cfg.get("stations"):
        cfg["stations"] = DEFAULT_STATIONS
    return cfg


def geocode(addr, app_key):
    """지번/도로명 주소 -> (lat, lon). 실패 시 None."""
    params = urllib.parse.urlencode({
        "version": "1",
        "format": "json",
        "coordType": "WGS84GEO",
        "fullAddr": addr,
    })
    req = urllib.request.Request(GEO_URL + "?" + params, method="GET")
    req.add_header("appKey", app_key)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return None, "지오코딩 요청 실패: %s" % e
    coords = (data.get("coordinateInfo") or {}).get("coordinate") or []
    if not coords:
        return None, "좌표 없음(주소 인식 실패)"
    c = coords[0]
    lat = c.get("newLat") or c.get("lat")
    lon = c.get("newLon") or c.get("lon")
    if not lat or not lon:
        return None, "좌표 필드 없음"
    return (float(lat), float(lon)), None


def walk_time(from_lat, from_lon, to_lat, to_lon, app_key, start_name="매물", end_name="역"):
    """보행자 경로 -> (초, m). 실패 시 (None, None)."""
    body = json.dumps({
        "startX": str(from_lon), "startY": str(from_lat),
        "endX": str(to_lon), "endY": str(to_lat),
        "startName": urllib.parse.quote(start_name),
        "endName": urllib.parse.quote(end_name),
        "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
        "searchOption": "0",
    }).encode("utf-8")
    url = PED_URL + "?version=1&format=json"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("appKey", app_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        if "totalTime" in props:
            return int(props["totalTime"]), int(props.get("totalDistance", 0))
    return None, None


def nearest_station(coord, stations, app_key):
    lat, lon = coord
    best = None
    for st in stations:
        sec, dist = walk_time(lat, lon, st["lat"], st["lon"], app_key,
                              end_name=st["name"])
        if sec is None:
            continue
        if best is None or sec < best["sec"]:
            best = {"station": st["name"], "sec": sec, "dist": dist}
        time.sleep(0.15)  # quota 예의
    return best


def main():
    args = sys.argv[1:]
    addresses = []
    if args and args[0] == "--file":
        with open(args[1], "r", encoding="utf-8") as f:
            addresses = [ln.strip() for ln in f if ln.strip()]
    else:
        addresses = [a for a in args if a.strip()]
    if not addresses:
        sys.exit("사용법: python walk_time.py \"주소1\" \"주소2\"  또는  --file addresses.txt")

    cfg = load_config()
    key = cfg["tmap_app_key"]
    limit = cfg["walk_limit_min"]
    band = cfg["review_band_min"]
    stations = cfg["stations"]

    print("주소 | 최근접역 | 도보(분) | 도보거리(m) | 판정(<=%d분, 재검토=%d~%d분)" % (
        limit, limit, limit + band))
    print("---|---|---|---|---")
    for addr in addresses:
        coord, err = geocode(addr, key)
        if err:
            print("%s | - | - | - | 확인불가(%s)" % (addr, err))
            continue
        best = nearest_station(coord, stations, key)
        if not best:
            print("%s | - | - | - | 경로계산 실패" % addr)
            continue
        minutes = round(best["sec"] / 60, 1)
        if minutes <= limit:
            verdict = "통과"
        elif minutes <= limit + band:
            verdict = "재검토(경계)"
        else:
            verdict = "제외(초과)"
        print("%s | %s | %s | %s | %s" % (
            addr, best["station"], minutes, best["dist"], verdict))


if __name__ == "__main__":
    main()
