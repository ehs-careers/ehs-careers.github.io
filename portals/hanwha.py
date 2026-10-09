"""한화그룹 통합 채용 플랫폼(한화인, hanwhain.com) 공고 목록 — 표준 라이브러리만 사용.

엔드포인트(인증 없음, JSON POST):
  https://hwadm.hanwhain.com/new-backend/portal/api/rcRecruit/search-rcrt
  본문 {"langCd":"ko","searchText":"","sdSeqList":null,"rtNrcrtYn":"","rtCarrYn":"",
        "rtIntnYn":"","rtPermanentWorkYn":"","rtTempWorkYn":"","djSeqList":null,
        "rjSeqList":null,"page":0,"size":N}
  응답 data = {list, hasNext, totalCount, filteredCount, page, size}
  - 화면(Vue SPA, /portal/js/PortalApply.*.js)의 loadRecruitList() 와 같은 호출. 화면은 size=20.
  - 목록 항목: sdNm(계열사) · rtNm(공고명) · rtAcptStrtDttm/rtAcptEndDttm · rtSeq · tagList
  - 목록에는 경력/신입 구분이 없어서, rtCarrYn/rtNrcrtYn/rtIntnYn='Y' 필터로 한 번씩 더 조회해
    rtSeq 별 구분을 붙인다(상세 get-rcrt 를 공고마다 부르지 않기 위함).
  - 상세 화면: https://www.hanwhain.com/portal/apply/recruit/detail?rtSeq=<rtSeq>
robots.txt: www.hanwhain.com/robots.txt 는 SPA 첫 화면을 돌려줌(= robots 규칙 없음).
"""
import json
import re
import time
import urllib.request

API = "https://hwadm.hanwhain.com/new-backend/portal/api/rcRecruit/search-rcrt"
DETAIL = "https://www.hanwhain.com/portal/apply/recruit/detail?rtSeq={}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 20
PAGE_SIZE = 50
DELAY = 4.0          # 같은 호스트 요청 간격(초)
MAX_PAGES = 20       # 안전장치: 50 x 20 = 1,000건

_last = [0.0]


def _post(body):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.hanwhain.com",
            "Referer": "https://www.hanwhain.com/",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # HTTPError 는 그대로 올라감
            data = json.loads(r.read().decode("utf-8"))
    finally:
        _last[0] = time.time()
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise RuntimeError(f"hanwhain 응답 형식이 바뀜: {str(data)[:200]}")
    return data["data"]


def _search_all(**flags):
    body = {"langCd": "ko", "searchText": "", "sdSeqList": None,
            "rtNrcrtYn": "", "rtCarrYn": "", "rtIntnYn": "",
            "rtPermanentWorkYn": "", "rtTempWorkYn": "",
            "djSeqList": None, "rjSeqList": None, "page": 0, "size": PAGE_SIZE}
    body.update(flags)
    items, total = [], None
    for page in range(MAX_PAGES):
        body["page"] = page
        d = _post(body)
        if total is None:
            total = d.get("filteredCount", d.get("totalCount"))
        lst = d.get("list") or []
        items.extend(lst)
        if not d.get("hasNext") or not lst:
            break
    else:
        raise RuntimeError(f"hanwhain: {MAX_PAGES}페이지를 넘음(무한 반복 의심)")
    return items, total


def _tag_text(tags):
    out = []
    for t in tags or []:
        if isinstance(t, dict):
            v = t.get("tagNm") or t.get("name") or next(
                (x for x in t.values() if isinstance(x, str) and x.strip()), "")
        else:
            v = str(t)
        if v:
            out.append(v.strip())
    return ", ".join(out)


def fetch():
    items, total = _search_all()
    if isinstance(total, int) and total != len(items):
        print(f"[hanwha] 경고: totalCount={total} 인데 받은 공고 {len(items)}건")

    # 경력/신입/인턴 구분(목록 응답에 없음) — 필터별로 한 번씩 더 조회
    kinds = {}
    for flag, label in (("rtCarrYn", "경력"), ("rtNrcrtYn", "신입"), ("rtIntnYn", "인턴")):
        try:
            sub, _ = _search_all(**{flag: "Y"})
        except Exception as e:  # 구분은 부가 정보 — 실패해도 목록은 돌려줌
            print(f"[hanwha] {label} 필터 조회 실패: {e}")
            continue
        for it in sub:
            kinds.setdefault(it.get("rtSeq"), []).append(label)

    out = []
    for it in items:
        seq = it.get("rtSeq")
        if seq is None:
            continue
        parts = [f"접수기간: {it.get('rtAcptStrtDttm') or ''} ~ {it.get('rtAcptEndDttm') or ''}"]
        if kinds.get(seq):
            parts.append("경력구분: " + "/".join(kinds[seq]))
        tags = _tag_text(it.get("tagList"))
        if tags:
            parts.append("태그: " + tags)
        out.append({
            "company": (it.get("sdNm") or "").strip(),
            "title": re.sub(r"<[^>]+>", "", it.get("rtNm") or "").strip(),
            "href": DETAIL.format(seq),
            "block": " | ".join(parts),
        })
    return out


EHS = re.compile(r"환경|안전|EHS|SHE|HSE|보건|화학물질|PSM")

if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    jobs = fetch()
    print("count:", len(jobs))
    for j in jobs[:5]:
        print(j)
    hits = [j for j in jobs if EHS.search(j["title"] + " " + j["block"])]
    print("EHS match:", len(hits))
    for j in hits:
        print("  -", j["company"], "|", j["title"], "|", j["href"])
