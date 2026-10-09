# -*- coding: utf-8 -*-
"""(1회용 기록) PC 의 radar.db·company_cache.json → 공개 저장소 state/ 로 복사해 클라우드 수집의 '어제 목록'으로 쓴다.
개인 값(현재 회사 대비 비교 cmp_*, 표시 mark, 기준 회사 캐시)은 지운다."""
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(os.path.expandvars(r"%LOCALAPPDATA%\JobRadar"))
(ROOT / "state").mkdir(exist_ok=True)
a = sqlite3.connect(f"file:{SRC / 'radar.db'}?mode=ro", uri=True)
b = sqlite3.connect(ROOT / "state" / "radar.db")
a.backup(b)
b.execute("UPDATE jobs SET cmp_salary='', cmp_size='', mark=''")
b.commit()
print("공고", b.execute("select count(*), sum(status='open') from jobs").fetchone())
b.execute("VACUUM")
b.close()
c = json.loads((SRC / "company_cache.json").read_text(encoding="utf-8"))
base = json.loads((ROOT / "settings.local.json").read_text(encoding="utf-8")).get("baseline", {}).get("company", "")
drop = [k for k in c if base and base in k]
for k in drop:
    c.pop(k)
(ROOT / "state" / "company_cache.json").write_text(json.dumps(c, ensure_ascii=False), encoding="utf-8")
print("회사 캐시", len(c), "곳 (기준 회사 항목 제외:", len(drop), ")")
