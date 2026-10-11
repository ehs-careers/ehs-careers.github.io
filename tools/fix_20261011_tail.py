# -*- coding: utf-8 -*-
"""(1회용 기록, 2026-10-11) 모음 검색 공고가 본문의 '검색:환경안전' 기록 때문에 제목 뒤에 '(환경안전)' 꼬리가 붙어 통과한 행을 정리.
꼬리를 뗀 원래 제목이 지금 규칙을 통과하지 못하면 '마감'으로 닫는다(지우지 않음 — verify 에 사유, 되돌리려면 status 를 open 으로).
사용: JOBRADAR_DATA=<radar.db 가 있는 폴더> python tools/fix_20261011_tail.py [--apply]"""
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "ml"))
import job_radar as jr  # noqa: E402
from relevance import sections_loose  # noqa: E402

TAIL = re.compile(r"\s*\((?:" + jr.API_FIELD.pattern + r")\)\s*$", re.I)
REASON = "판정 오류로 정리(2026-10-11: 모음 검색 기록을 직무로 읽은 버그)"


GENERIC = set("채용 모집 공고 상시채용 수시채용 공개채용 신규채용 경력 경력직 경력사원 신입 신입사원 사원 인턴 채용전환형 전환형 각 부문 부문별 분야 분야별 직군 직무 전 인재 pool 상시 수시 보훈 취업지원대상자 하반기 상반기 및 의".split())


def generic(base, company):
    """꼬리를 뗀 나머지가 '각 부문별 상시채용'·'채용전환형 인턴 모집' 같은 일반 채용 문구뿐이면 원래 제목의 분야 괄호로 본다"""
    t = re.sub(r"20\d\d\s*년?|\d+\s*월|\d+\s*분기|[\[\]()|_\-·/]", " ", base)
    for v in [company or ""] + re.findall(r"[가-힣A-Za-z]+", company or ""):
        if len(v) >= 2:
            t = t.replace(v, " ")
    words = [w for w in re.findall(r"[가-힣A-Za-z]+", t) if w.lower() not in GENERIC]
    return not words


def main(apply):
    s = jr.load_settings()
    con = sqlite3.connect(jr.DB_PATH)
    rows = con.execute("SELECT id, company, title, source, status FROM jobs WHERE status='open' AND (source LIKE '%(검색)%' OR source LIKE '%대조)%')").fetchall()
    bad = []
    for i, co, title, src, st in rows:
        m = TAIL.search(title or "")
        if not m:
            continue
        base = title[:m.start()]
        if not sections_loose({"title": base, "company": co}, s) and not generic(base, co):
            bad.append((i, co, title))
    print(f"모음 검색 진행 중 {len(rows)}건 중 정리 대상 {len(bad)}건")
    for i, co, t in bad[:15]:
        print("  ", co[:14], "|", t[:60])
    if apply and bad:
        con.executemany("UPDATE jobs SET status='closed', verify=? WHERE id=?", [(REASON, i) for i, _, _ in bad])
        con.commit()
        print("적용:", len(bad), "건 마감 처리")


if __name__ == "__main__":
    main("--apply" in sys.argv)
