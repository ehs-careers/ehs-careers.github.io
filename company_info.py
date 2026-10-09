# -*- coding: utf-8 -*-
"""회사 비교: 공고 회사를 사용자의 현재 회사(settings.baseline — 개인 값은 settings.local.json)와 연봉·규모로 비교한다.

출처: 사람인 기업정보
- 기업형태(1000대기업·대기업·중견 등, NICE평가정보) · 사원수(국민연금)  ← /company-info/view?csn=
- 평균연봉(사람인 추정: 국민연금·공시 등 기반, 계약직·임원 포함) · 최저/최고 · 신뢰도  ← /company-info/view-inner-salary?csn=
회사 정보는 %LOCALAPPDATA%\\JobRadar\\company_cache.json 에 30일 보관 (사람인 요청을 늘리지 않기 위해).
"""
import html
import json
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SARAMIN = "https://www.saramin.co.kr"
GAP = 4.0
TTL_DAYS = 30


def _get(url):
    from verify_jobs import _retry, _get as g
    return _retry(g, url)


def _flat(h):
    h = re.sub(r"<script.*?</script>|<style.*?</style>", " ", h, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def norm(s):
    return re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\s|[()·.,（）]", "", s or "").lower()


def csn_from_page(page):
    m = re.search(r"company-info/view\?csn=([A-Za-z0-9%=+/]+)", page)
    return urllib.parse.unquote(m.group(1)) if m else None


def find_csn(company):
    """사람인 기업 검색에서 이름이 가장 잘 맞는 회사의 csn"""
    q = re.sub(r"\(주\)|㈜|주식회사|→.*$", "", company or "").strip()
    if not q:
        return None
    page = _get(f"{SARAMIN}/zf_user/search/company?searchword={urllib.parse.quote(q)}")
    n = norm(q)
    best, score = None, 0
    for m in re.finditer(r'href="/zf_user/company-info/view[^"]*csn=([^"&]+)[^"]*"[^>]*>(.*?)</a>', page, re.S):
        name = norm(_flat(m.group(2)))
        if not name:
            continue
        s = 3 if name == n else 2 if (n in name or name in n) else 0
        if s > score:
            best, score = html.unescape(m.group(1)), s
            if s == 3:
                break
    return best if score >= 2 else None


def fetch_profile(csn):
    t = _flat(_get(f"{SARAMIN}/zf_user/company-info/view?csn={urllib.parse.quote(csn, safe='')}"))
    out = {"csn": csn}
    m = re.search(r"기업형태:\s*(.+?)\s+기업형태", t)
    if m:
        out["form"] = m.group(1).strip()
    m = re.search(r"([\d,]+)\s*명\s*출처:\s*국민연금", t) or re.search(r"전체\s*사원수\s*([\d,]+)\s*명", t)
    if m:
        out["employees"] = int(m.group(1).replace(",", ""))
    time.sleep(GAP)
    s = _flat(_get(f"{SARAMIN}/zf_user/company-info/view-inner-salary?csn={urllib.parse.quote(csn, safe='')}"))
    m = re.search(r"(\d{4})년\s*평균연봉.*?([\d,]{3,})\s*만원\s*최저\s*([\d,]{3,})\s*만원\s*최고\s*([\d,]{3,})\s*만원", s)
    if m:
        out.update(salary_year=int(m.group(1)), avg_salary=int(m.group(2).replace(",", "")),
                   salary_min=int(m.group(3).replace(",", "")), salary_max=int(m.group(4).replace(",", "")))
    m = re.search(r"알리오\s*정보\s*등\s*(매우\s*높음|높음|보통|매우\s*낮음|낮음)", s)
    if m:
        out["salary_reliability"] = re.sub(r"\s+", "", m.group(1))
    out["fetched"] = datetime.now(KST).strftime("%Y-%m-%d")
    return out


def is_big(p):
    return bool(re.search(r"대기업", p.get("form", "")))


def compare(p, base):
    """(연봉 비교, 규모 비교) 문구. p·base: fetch_profile 결과"""
    sal = "정보 없음"
    if p.get("avg_salary") and base.get("avg_salary"):
        r = p["avg_salary"] / base["avg_salary"] - 1
        sal = f"높음 +{round(r * 100)}%" if r > 0.05 else f"낮음 {round(r * 100)}%" if r < -0.05 else "비슷"
    size = "정보 없음"
    if p.get("employees") and base.get("employees"):
        e = p["employees"] / base["employees"]
        if is_big(p) and e >= 1.3:
            size = "더 큼"
        elif not is_big(p) and is_big(base):
            size = "작음"
        else:
            size = "더 큼" if e >= 1.3 else "작음" if e < 0.7 else "비슷"
    elif p.get("form") and is_big(base):
        size = "비슷" if is_big(p) else "작음"
    return sal, size


class Cache:
    def __init__(self, path):
        self.path = path
        try:
            self.d = json.load(open(path, encoding="utf-8"))
        except Exception:
            self.d = {}

    def get(self, company):
        v = self.d.get(norm(company))
        if v and v.get("fetched", "") >= (datetime.now(KST) - timedelta(days=TTL_DAYS)).strftime("%Y-%m-%d"):
            return v
        return None

    def put(self, company, prof):
        self.d[norm(company)] = prof
        json.dump(self.d, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def profile_for(company, cache, csn=None):
    """회사명(+알면 csn) → 프로필 (캐시 우선). 못 찾으면 {'missing': True}"""
    v = cache.get(company)
    if v:
        return v
    csn = csn or find_csn(company)
    if not csn:
        prof = {"missing": True, "fetched": datetime.now(KST).strftime("%Y-%m-%d")}
    else:
        time.sleep(GAP)
        prof = fetch_profile(csn)
    cache.put(company, prof)
    return prof
