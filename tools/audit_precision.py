# -*- coding: utf-8 -*-
"""분야 규칙 점검: 사람이 하나씩 본 정답지(docs/audit/gold_*.json, PC 전용 — 공개 저장소에 없음)에 지금 규칙을 다시 적용해
'빼야 하는데 남은 것(오탐)'과 '남아야 하는데 빠진 것(누락)'을 센다. 규칙(settings.json profiles)을 고친 뒤 실행.
사용: python tools/audit_precision.py [정답지 경로]   (누락이 1건이라도 있으면 exit 1 — 재현율 우선)"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ml"))
import relevance as R  # noqa: E402


def main(paths):
    st = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    paths = paths or sorted((ROOT / "docs" / "audit").glob("gold_*.json"))
    if not paths:
        print("정답지 없음(docs/audit/gold_*.json)"); return 0
    fp, fn, n = [], [], 0
    for p in paths:
        for it in json.loads(Path(p).read_text(encoding="utf-8"))["items"]:
            n += 1
            got = bool(R.sections({"title": it["title"], "company": it["company"]}, st, it.get("size_rank")))
            if got and not it["keep"]:
                fp.append(it)
            elif it["keep"] and not got:
                fn.append(it)
    keep = sum(1 for p in paths for it in json.loads(Path(p).read_text(encoding="utf-8"))["items"] if it["keep"])
    print(f"정답지 {n}건 (남겨야 {keep} · 빼야 {n - keep})")
    print(f"오탐(빼야 하는데 남음) {len(fp)}건 · 누락(남아야 하는데 빠짐) {len(fn)}건")
    for it in fp:
        print(f"  오탐  {it['company'][:14]} | {it['title'][:60]} ← {it['why']}")
    for it in fn:
        print(f"  누락  {it['company'][:14]} | {it['title'][:60]}")
    return 1 if fn else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
