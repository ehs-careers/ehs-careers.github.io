# -*- coding: utf-8 -*-
"""메이저 그룹(SK·삼성·현대차·HD현대·LG·한화 …) 채용 사이트의 오늘 공고를 '전부' 받아,
환경·안전 낱말이 제목·본문 어디든 있는 공고가 수집기 판정(judge + 모델)에서 하나라도 버려지는지 본다.
사용: python tools/audit_groups.py  → 버려진 공고와 이유를 출력 (사용자 지시 2026-10-09: "메이저 대기업은 하나도 놓치지 않게")"""
import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "ml"))
import job_radar as jr  # noqa: E402

WIDE = re.compile(r"환경|안전|보건|위생|EHS|SHE|HSE|ESH|PSM|화학물질|화관법|위험물|중대재해|소방|방재|온실가스|대기|수질|폐수|폐기물", re.I)
ADAPTERS = [("SK", "sk", None), ("삼성", "samsung", None), ("LG", "lg", None), ("한화", "hanwha", None),
            ("현대자동차그룹", "groups", "현대자동차"), ("HD현대", "groups", "HD현대"), ("롯데", "groups", "롯데"), ("CJ", "groups", "CJ"),
            ("두산", "groups", "두산"), ("효성", "groups", "효성"), ("LS", "groups", "LS")]


def main():
    s = jr.load_settings()
    t = jr.today()
    from relevance import score
    keep_t = float(s.get("ml", {}).get("keep_threshold", 0.10))
    total = dropped = 0
    for name, mod, key in ADAPTERS:
        src = {"type": "api", "name": f"{name} (점검)", "url": "https://x", "company": name, "group": "대기업 계열",
               "adapter": f"{mod}:{key}" if key else mod}
        try:
            rows = jr.run_adapter(src)
        except Exception as e:
            print(f"[{name}] 받기 실패: {e}"); continue
        hits = [r for r in rows if WIDE.search((r.get("title") or "") + " " + (r.get("block") or ""))]
        print(f"\n[{name}] 전체 {len(rows)}건 · 환경·안전 낱말 {len(hits)}건")
        for r in hits:
            total += 1
            item = {"title": r.get("title", ""), "block": r.get("block", ""), "href": r.get("href", ""), "company": r.get("company", "")}
            j, why = jr.judge(src, item, s, t)
            if j:
                p = score([j])[0]
                if p < keep_t and "안전" not in j.get("sec", []):
                    j, why = None, f"모델 점수 {p:.2f} < {keep_t}"
            mark = "  ✓" if j else "  ✗"
            if not j:
                dropped += 1
            print(f"{mark} {r.get('company', '')[:14]:14} | {r.get('title', '')[:50]:50} | {'' if j else why} {('· 분야 ' + ','.join(j['sec'])) if j else ''}")
    print(f"\n환경·안전 낱말 공고 {total}건 중 버려짐 {dropped}건 (버려진 것은 이유를 보고 진짜 다른 직무인지 확인)")


if __name__ == "__main__":
    main()
