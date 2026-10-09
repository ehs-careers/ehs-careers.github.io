"""삼성 채용 홈페이지(samsungcareers.com) 공고 수집 — 표준 라이브러리만 사용.

엔드포인트 (2026-10-08 실측, 로그인 불필요):
  - 목록: POST https://www.samsungcareers.com/hr/list.data
      form: currentPageNo=N&intNo=0&strVal=&strTxt=&strKey=&strCompany=&strType=&strOrderBy=&strEntity=
      응답: HTML 조각 (<li> 카드). <input class="divCnt" data-value="총건수" data-max="총페이지">
  - 상세: GET https://www.samsungcareers.com/recruit/detail.data?seqno=<seq>&strCode=
      응답: JSON {success, data:{result:{...}, items:[{titleKr, taskKr, qlfctKr, workPlaceKr...}]}}
  - 공고 직접 링크(사이트 '공유' 버튼과 같은 형식): https://www.samsungcareers.com/hr/?no=<seq>
robots.txt 없음(/robots.txt → 오류 페이지로 302).
"""
import html
import json
import re
import time
import urllib.parse
import urllib.request

BASE = "https://www.samsungcareers.com"
LIST_URL = BASE + "/hr/list.data"
DETAIL_URL = BASE + "/recruit/detail.data"
TIMEOUT = 20
DELAY = 4.0          # 같은 호스트 요청 간격(초)
MAX_PAGES = 20
MAX_DETAILS = 40     # 상세 조회 상한(사이트당 요청 60회 미만 유지)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

_last = [0.0]


def _request(url, data=None):
    wait = DELAY - (time.monotonic() - _last[0])
    if _last[0] and wait > 0:
        time.sleep(wait)
    headers = {"User-Agent": UA, "Referer": BASE + "/hr/",
               "Accept-Language": "ko-KR,ko;q=0.9"}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded;charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # HTTPError 는 그대로 올라감
            return r.read().decode("utf-8", "replace")
    finally:
        _last[0] = time.monotonic()


def _text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _parse_list(page_html):
    m = re.search(r'class="divCnt"[^>]*data-value="(\d+)"[^>]*data-max="(\d+)"', page_html)
    total, max_page = (int(m.group(1)), int(m.group(2))) if m else (0, 1)
    items = []
    for li in re.findall(r"<li>(.*?)</li>", page_html, re.S):
        a = re.search(r'<a [^>]*data-value="([\d,]+)"', li)
        if not a:
            continue
        seq = int(a.group(1).replace(",", ""))
        company = _text((re.search(r'class="company">(.*?)</p>', li, re.S) or [None, ""])[1])
        title = _text((re.search(r'class="title">(.*?)</h3>', li, re.S) or [None, ""])[1])
        info = re.search(r'class="info">(.*?)</p>', li, re.S)
        spans = [_text(x) for x in re.findall(r"<span[^>]*>(.*?)</span>", info.group(1), re.S)] if info else []
        flags = [_text(x) for x in re.findall(r'<span class="flag[^"]*">(.*?)</span>', li, re.S)]
        items.append({"seq": seq, "company": company, "title": title,
                      "info": [s for s in spans if s], "flags": [f for f in flags if f]})
    return total, max_page, items


def _fmt_dt(s):
    s = str(s or "")
    return f"{s[:4]}.{s[4:6]}.{s[6:8]} {s[8:10]}:{s[10:12]}" if len(s) >= 12 else s


def _detail_text(seq):
    raw = _request(DETAIL_URL + "?" + urllib.parse.urlencode({"seqno": seq, "strCode": ""}))
    d = json.loads(raw)
    if not d.get("success"):
        return ""
    data = d.get("data") or {}
    res = data.get("result") or {}
    parts = []
    if res.get("startdate") or res.get("enddate"):
        parts.append(f"접수기간: {_fmt_dt(res.get('startdate'))} ~ {_fmt_dt(res.get('enddate'))}")
    if res.get("qlfctKr"):
        parts.append("공통 자격: " + _text(res["qlfctKr"]))
    for it in data.get("items") or []:
        seg = [f"[모집분야] {_text(it.get('titleKr'))}"]
        for key, label in (("taskKr", "담당업무"), ("qlfctKr", "자격요건"), ("favorKr", "우대사항"),
                           ("workPlaceKr", "근무지"), ("memoKr", "비고")):
            if it.get(key):
                seg.append(f"{label}: {_text(it[key])}")
        parts.append(" / ".join(seg))
    return "\n".join(parts)


def fetch(with_detail=True):
    form = {"currentPageNo": 1, "intNo": 0, "strVal": "", "strTxt": "", "strKey": "",
            "strCompany": "", "strType": "", "strOrderBy": "", "strEntity": ""}
    rows, seen, page, max_page = [], set(), 1, 1
    while page <= min(max_page, MAX_PAGES):
        form["currentPageNo"] = page
        total, max_page, items = _parse_list(_request(LIST_URL, form))
        if not items:
            break
        for it in items:
            if it["seq"] not in seen:
                seen.add(it["seq"])
                rows.append(it)
        page += 1

    out = []
    for i, it in enumerate(rows):
        block = [it["company"], it["title"], " · ".join(it["info"]), " · ".join(it["flags"])]
        if with_detail and i < MAX_DETAILS:
            try:
                block.append(_detail_text(it["seq"]))
            except Exception as e:  # 상세 실패는 목록 정보만으로 진행
                block.append(f"(상세 조회 실패: {type(e).__name__})")
        out.append({
            "company": it["company"],
            "title": it["title"],
            "href": f"{BASE}/hr/?no={it['seq']}",
            "block": "\n".join(b for b in block if b),
        })
    return out


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pat = re.compile(r"환경|안전|EHS|SHE|HSE|보건|화학물질|PSM")
    jobs = fetch()
    print("count:", len(jobs))
    for j in jobs[:5]:
        print("-", j["company"], "|", j["title"], "|", j["href"])
        print("   ", j["block"][:200].replace("\n", " / "))
    t = [j for j in jobs if pat.search(j["title"])]
    b = [j for j in jobs if pat.search(j["block"])]
    print(f"EHS match: title {len(t)}, block {len(b)}")
    for j in b:
        print("  *", j["company"], "|", j["title"], "| title-match" if j in t else "| block-only")
