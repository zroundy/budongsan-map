# -*- coding: utf-8 -*-
"""부동산 오픈채팅 매물 텍스트 파서. 자유형식 블록 -> 구조화 레코드."""
import re

DONG_TO_GU = {
    "봉천동": "관악구", "신림동": "관악구", "낙성대동": "관악구", "청룡동": "관악구",
    "은천동": "관악구", "성현동": "관악구", "중앙동": "관악구", "인헌동": "관악구",
    "남현동": "관악구", "서원동": "관악구", "신원동": "관악구", "서림동": "관악구",
    "삼성동": "관악구", "미성동": "관악구", "난향동": "관악구", "조원동": "관악구",
    "대학동": "관악구",
    "상도동": "동작구", "대방동": "동작구", "노량진동": "동작구", "본동": "동작구",
    "영등포동": "영등포구", "여의도동": "영등포구",
}

RE_WOLSE = re.compile(r"월\s*[\d,]+\s*/\s*\d+")
RE_JEON = re.compile(r"전\s*([\d,]+)")
RE_EOK = re.compile(r"([\d]+(?:\.\d+)?)\s*억\s*([\d,]*)")
RE_MGMT = re.compile(r"관리비\s*([\d,]+)")
RE_JIBUN = re.compile(r"(\d+-\d+)")
RE_DONG = re.compile(r"([가-힣]+동)")
RE_GU = re.compile(r"([가-힣]+구)")
RE_HO = re.compile(r"(\d+)\s*호")
RE_FLOOR = re.compile(r"(반지하|지하층|지층|B1층|-?\d+\s*층)")


def _num(s):
    return int(s.replace(",", "").strip())


def parse_deposit(text):
    """전세 보증금(만원) 반환. 월세면 None + is_wolse=True."""
    if RE_WOLSE.search(text):
        return None, True
    m = RE_EOK.search(text)
    if m:
        eok = float(m.group(1))
        rest = m.group(2)
        val = int(round(eok * 10000))
        if rest:
            val += _num(rest)
        return val, False
    m = RE_JEON.search(text)
    if m:
        return _num(m.group(1)), False
    return None, False


def _amount_lines(lines):
    """금액(전/월) 포함 줄 인덱스."""
    idx = []
    for i, ln in enumerate(lines):
        if RE_WOLSE.search(ln) or RE_JEON.search(ln) or RE_EOK.search(ln):
            idx.append(i)
    return idx


def _floor_info(text):
    banjiha = bool(re.search(r"반지하|지하(?!철)|지층|B1층|B1호|-1\s*층", text))
    m = RE_FLOOR.search(text)
    floor_txt = m.group(1).strip() if m else ""
    return banjiha, floor_txt


def _make_record(block_text, addr_src, amount_src):
    gu_m = RE_GU.search(addr_src)
    dong_m = RE_DONG.search(addr_src)
    jibun_m = RE_JIBUN.search(addr_src)
    dong = dong_m.group(1) if dong_m else ""
    gu = gu_m.group(1) if gu_m else DONG_TO_GU.get(dong, "")
    jibun = jibun_m.group(1) if jibun_m else ""
    ho_m = RE_HO.search(amount_src) or RE_HO.search(addr_src)
    ho = ho_m.group(1) if ho_m else ""

    deposit, is_wolse = parse_deposit(amount_src)
    mgmt_m = RE_MGMT.search(amount_src) or RE_MGMT.search(block_text)
    mgmt = _num(mgmt_m.group(1)) if mgmt_m else None

    banjiha, floor_txt = _floor_info(addr_src + " " + amount_src)
    form_raw = "분리형 원룸" if "분리형" in block_text else (
        "큰원룸" if "큰원룸" in block_text else ("원룸" if "원룸" in block_text else ""))
    rtype = "분리형" if "분리형" in block_text else ("개방형" if "원룸" in block_text else "미표기")
    deposit_ins = "보증보험" in block_text
    loan = "HF 버팀목 표기" if re.search(r"버팀목|HF", block_text, re.I) else "미표기(중개사 확인)"

    # 층 표기 모순: N층인데 호수 첫자리가 다른 경우(1층/201호 등)
    floor_conflict = False
    fm = re.search(r"(\d+)\s*층", floor_txt)
    if fm and ho and len(ho) >= 3:
        if fm.group(1) != ho[0]:
            floor_conflict = True

    parts = [p for p in [gu, dong, jibun] if p]
    address = "서울 " + " ".join(parts) if parts else ""
    bld_m = re.search(r"[가-힣A-Za-z]+(타워|하우스|빌|빌라|오피스텔|아이리스|명가|온누리)", addr_src)
    building = bld_m.group(0) if bld_m else ""
    name_bits = [x for x in [dong, jibun, building, (ho + "호" if ho else "")] if x]
    name = " ".join(name_bits) if name_bits else addr_src.strip()[:40]

    return {
        "name": name, "address": address, "gu": gu, "dong": dong, "jibun": jibun,
        "deposit": deposit, "is_wolse": is_wolse, "mgmt": mgmt,
        "type": rtype, "form_raw": form_raw or rtype,
        "deposit_ins": deposit_ins, "loan": loan,
        "floor": floor_txt or "미표기", "banjiha": banjiha,
        "floor_conflict": floor_conflict,
        "raw": block_text.strip(),
    }


def parse_text(text):
    """전체 텍스트 -> 레코드 리스트."""
    blocks = re.split(r"\n\s*\n", text.strip())
    records = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        amt_idx = _amount_lines(lines)
        if len(amt_idx) <= 1:
            records.append(_make_record(block, lines[0], block))
        else:
            # 멀티 유닛: 첫 줄(주소/건물)을 공유, 금액 줄마다 레코드
            addr_src = lines[0]
            for i in amt_idx:
                rec = _make_record(block, addr_src + " " + lines[i], lines[i])
                records.append(rec)
    return records
