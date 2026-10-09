"""SK 그룹 채용 포털(skcareers.com) 공고 목록 — 표준 라이브러리만 사용.

엔드포인트: POST https://www.skcareers.com/Recruit/GetRecruitList (form-urlencoded)
  - /Recruit 페이지의 jQuery 가 부르는 것과 같은 요청. 한 번에 전체 목록(totalCount 건)을 돌려주며
    페이지 나눔은 화면(더보기)에서만 함 → 요청 1회.
  - 모든 필드(sort 포함)를 보내야 함. sort 만 보내면 404.
  - 언어는 Accept-Language 로 정해짐(없으면 영문 회사명·날짜).
  - 상세: https://www.skcareers.com/Recruit/Detail/<noticeID>
SK하이닉스·SK이노베이션 계열 등도 이 목록에 포함됨(corpName).
robots.txt: /Recruit 허용, '/*?searchText=' 만 금지(여기서는 POST 본문이라 해당 없음).
"""
import json
import re
import urllib.parse
import urllib.request

BASE = "https://www.skcareers.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")
TIMEOUT = 20

RECRUIT_TYPE = {"Experienced": "경력", "New": "신입", "Entry": "신입",
                "Intern": "인턴", "Any": "경력무관"}


def _post_list():
    form = {"sort": "2", "searchText": "", "corpCode": "", "jobRole": "",
            "recruitType": "", "workingType": "", "workingRegion": ""}
    req = urllib.request.Request(
        BASE + "/Recruit/GetRecruitList",
        data=urllib.parse.urlencode(form).encode(),
        headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": BASE + "/Recruit",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ko-KR,ko;q=0.9",
        })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # HTTPError 는 그대로 올라감
        data = json.loads(r.read().decode("utf-8"))
    if not data.get("success"):
        raise RuntimeError("skcareers GetRecruitList success=false")
    return data


def fetch():
    data = _post_list()
    out = []
    for d in data.get("list") or []:
        nid = str(d.get("noticeID") or "").strip()
        if not nid:
            continue
        rtype = d.get("recruitType") or ""
        parts = [
            f"접수기간: {d.get('start', '')} ~ {d.get('end', '')}",
            f"남은일: {d.get('remainDay')}",
            f"경력구분: {RECRUIT_TYPE.get(rtype, rtype)}",
            f"고용형태: {d.get('workingType') or ''}",
            f"근무지: {d.get('workingArea') or ''}",
            f"직무: {d.get('jobRole') or ''}",
        ]
        out.append({
            "company": (d.get("corpName") or "").strip(),
            "title": re.sub(r"<[^>]+>", "", d.get("title") or "").strip(),
            "href": f"{BASE}/Recruit/Detail/{urllib.parse.quote(nid)}",
            "block": " | ".join(parts),
        })
    total = data.get("totalCount")
    if isinstance(total, int) and total != len(out):
        # 서버가 목록을 잘라 보냈을 가능성 — 조용히 넘기지 않고 알림
        print(f"[sk] 경고: totalCount={total} 인데 받은 공고 {len(out)}건")
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
