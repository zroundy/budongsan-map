#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로컬 서버: 매물 텍스트 붙여넣기 -> 파싱 -> 필터 -> 지오코딩 + 도보시간 -> JSON.
브라우저(index.html)가 이 서버로 요청하므로 Tmap CORS 문제 없음. 배포 불필요.

실행: python server.py   (기본 포트 8765)
브라우저: http://localhost:8765
"""
import json
import math
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(BASE)
sys.path.insert(0, BASE)
from walk_time import load_config, geocode, nearest_station  # noqa: E402
from parser import parse_text  # noqa: E402

PORT = int(os.environ.get("PORT", 8765))
HOST = os.environ.get("HOST", "127.0.0.1")
INDEX = os.path.join(SKILL_DIR, "index.html")


# 정렬 기준지(회사 방향). 각 매물을 이 지점에서 가까운 순으로 나열.
DEST = {"name": "선릉역", "lat": 37.50450, "lon": 127.04879}
STATUS_ORDER = {"통과": 0, "재검토": 1, "확인필요": 2}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(r * 2 * math.asin(math.sqrt(a)), 2)


def status_of(walk_min, limit, band, review_reason):
    reasons = []
    st = "통과"
    if walk_min is None:
        st, reasons = "확인필요", ["도보 계산 실패"]
    elif walk_min > limit + band:
        st, reasons = "제외", ["도보 %s분(초과)" % walk_min]
    elif walk_min > limit:
        st, reasons = "재검토", ["도보 %s분(경계)" % walk_min]
    if review_reason:
        if st == "통과":
            st = "재검토"
        reasons.append(review_reason)
    return st, reasons


def process(text, params, cfg):
    key = cfg["tmap_app_key"]
    stations = cfg["stations"]
    dep_max = int(params.get("deposit_max", 15000))
    mgmt_max = int(params.get("mgmt_max", 10))
    limit = int(params.get("walk_limit", cfg["walk_limit_min"]))
    band = int(params.get("review_band", cfg["review_band_min"]))

    records, excluded = [], []
    for r in parse_text(text):
        base = {k: r[k] for k in ("name", "address", "deposit", "mgmt", "type",
                                  "form_raw", "deposit_ins", "loan", "floor")}
        # 텍스트 하드 필터
        if r["is_wolse"]:
            excluded.append({**base, "reason": "월세/반전세"}); continue
        if r["deposit"] is None:
            excluded.append({**base, "reason": "금액 파싱 실패"}); continue
        if r["deposit"] > dep_max:
            excluded.append({**base, "reason": "전세가 초과(%d만원)" % r["deposit"]}); continue
        if r["mgmt"] is not None and r["mgmt"] > mgmt_max:
            excluded.append({**base, "reason": "관리비 초과(%d만원)" % r["mgmt"]}); continue
        if r["banjiha"]:
            excluded.append({**base, "reason": "반지하"}); continue
        if not r["address"]:
            excluded.append({**base, "reason": "주소 인식 실패"}); continue

        # 지오코딩 + 도보
        coord, err = geocode(r["address"], key)
        lat = lon = station = walk_min = None
        if coord:
            lat, lon = coord
            nb = nearest_station(coord, stations, key)
            if nb:
                station, walk_min = nb["station"], round(nb["sec"] / 60, 1)
        review = "층 표기 모순 → 반지하 여부 확인" if r["floor_conflict"] else ""
        st, reasons = status_of(walk_min, limit, band, review)
        dest_km = haversine_km(lat, lon, DEST["lat"], DEST["lon"]) if lat else None
        rec = {**base, "lat": lat, "lon": lon, "station": station,
               "walk_min": walk_min, "status": st, "reasons": reasons,
               "dest_name": DEST["name"], "dest_km": dest_km}
        if st == "제외":
            excluded.append({**base, "reason": reasons[0] if reasons else "도보 초과"})
        else:
            records.append(rec)
    # 통과 → 재검토 순, 각 그룹 내에서는 선릉역에서 가까운 순(직선거리 오름차순)
    records.sort(key=lambda x: (STATUS_ORDER.get(x["status"], 3),
                                x["dest_km"] if x["dest_km"] is not None else 9999))
    return {"records": records, "excluded": excluded,
            "limit": limit, "band": band, "dest_name": DEST["name"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(INDEX, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/process":
            self._send(404, "{}"); return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            cfg = load_config()
            result = process(payload.get("text", ""), payload.get("params", {}), cfg)
            self._send(200, json.dumps(result, ensure_ascii=False))
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    is_deploy = os.environ.get("PORT") is not None
    url = "http://localhost:%d" % PORT
    print("부동산 매물 지도 서버 실행 중:", HOST, PORT)
    print("종료하려면 이 창에서 Ctrl+C")
    if not is_deploy:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
