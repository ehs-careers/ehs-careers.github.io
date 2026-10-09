# -*- coding: utf-8 -*-
"""화면용 중복 합치기 (2026-10-09 사용자: "중복되어서 올라오는 것도 있는 것 같은데").
같은 공고가 사람인·잡코리아·그룹 API·예전 클라우드 알림에 따로 올라와 회사명 표기만 다른 경우를 한 장으로 합친다.
  - 같은 공고: 제목 핵심(꺾쇠 [..]·회사명 제거, 괄호 안은 유지 — '(안전관리)'/'(보건관리)'는 다른 공고)이 같고 회사가 같은 회사
  - 같은 회사: 정규화 이름이 같거나 한쪽이 다른 쪽을 포함, 또는 표기 변환(에이치디→HD, 엘에스→LS …) 후 같음, 또는 영문(한글) 표기
  - 대표 공고: 원문 확인 > 공고 직접 링크 > 클라우드 알림 아님 > 마감일 있음. 처음 본 날은 묶음에서 가장 이른 날(NEW 가 다시 뜨지 않게)
  - 나머지는 대표의 also=[{site, link}] 와 aliases=[id] 로 남긴다(화면이 예전 표시를 대표로 옮김)
DB 는 건드리지 않는다(되돌리기 쉬움)."""
import re

KO_EN = [("에이치디", "hd"), ("엘에스", "ls"), ("에스케이", "sk"), ("엘지", "lg"), ("지에스", "gs"), ("씨제이", "cj"), ("케이씨씨", "kcc"),
         ("디엘", "dl"), ("에이치엘", "hl"), ("엘엑스", "lx"), ("케이티", "kt"), ("에쓰오일", "soil"), ("에스오일", "soil")]


def norm_co(s):
    s = (s or "").split("→")[0]
    s = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|유한책임회사|\s|[·.,\-&]", "", s).lower()
    for ko, en in KO_EN:
        s = s.replace(ko, en)
    return s


def co_variants(s):
    """'LS MnM(엘에스엠앤엠)' → {'lsmnm(lsmnm)', 'lsmnm', 'lsmnm'} 처럼 괄호 안·밖 표기를 모두"""
    n = norm_co(s)
    out = {re.sub(r"[()（）]", "", n)}
    m = re.match(r"^(.*?)[(（](.*?)[)）]$", n)
    if m:
        out |= {m.group(1), m.group(2)}
    return {x for x in out if len(x) >= 2}


def same_company(a, b):
    va, vb = co_variants(a), co_variants(b)
    return any(x == y or (len(min(x, y, key=len)) >= 2 and (x in y or y in x)) for x in va for y in vb)


def title_core(t, company):
    t = re.sub(r"\[[^\]]*\]", " ", t or "")
    for v in co_variants(company) | {re.sub(r"\(주\)|㈜|주식회사|\s", "", (company or "").split("→")[0])}:
        if len(v) >= 2:
            t = re.sub(re.escape(v), " ", t, flags=re.I)
    t = re.sub(r"\(주\)|㈜|주식회사", " ", t)
    return re.sub(r"[^0-9a-z가-힣()]", "", t.lower()).replace("()", "")


def same_title(a, b):
    if a == b:
        return True
    s, l = sorted((a, b), key=len)
    return len(s) >= 10 and l.startswith(s) and len(l) - len(s) <= 4  # '…채용' vs '…채용중' 정도의 꼬리 차이만


def site_of(link):
    for k, n in (("saramin", "사람인"), ("jobkorea", "잡코리아"), ("jasoseol", "자소설닷컴"), ("incruit", "인크루트"),
                 ("catch.co.kr", "캐치"), ("linkedin", "링크드인"), ("recruiter.co.kr", "그룹 채용 사이트")):
        if k in (link or ""):
            return n
    return "회사 채용 사이트"


def score(j):
    direct = bool(re.search(r"rec_idx=|GI_Read|/jobs/|/recruit/\d|detail|view\?|jobnotice|/o/\d|seqno|no=\d", j.get("link") or ""))
    return ((("원문 확인" in (j.get("verify") or "")) and "필요" not in (j.get("verify") or "")) * 4 + direct * 2
            + (j.get("source") != "클라우드 알림") * 1 + bool(re.match(r"\d{4}-", j.get("deadline") or "")) * 1)


# ---- 낱말 비교 (2026-10-09 2차: 순서·날짜·칸막이만 다른 같은 공고) ----
STOP = set("채용 모집 공고 경력 경력직 경력사원 신입 신입사원 사원 담당 담당자 인재 직원 정규직 수시 상시 공개 공개채용 수시채용 하반기 상반기 및 의 건 중 각 부문 부문별 분야 직무 근무 인원 채용중".split())
FIELD = ["환경", "안전", "보건", "위생", "소방", "방재", "공정", "화학", "위험물", "대기", "수질", "폐기물", "온실", "산업", "중대재해", "기획", "설계", "분석",
         "ehs", "she", "hse", "hseq", "esg", "psm", "인턴", "신입", "팀장", "그룹장", "파트장", "수석", "책임", "선임", "과장", "차장", "부장", "주니어", "시니어", "manager", "engineer", "specialist"]


def tokens(title, company):
    t = re.sub(r"\d{2,4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}.*$", " ", title or "")      # '2026/09/29 ~ …' 같은 날짜 꼬리
    t = re.sub(r"20\d\d\s*년?|\d+\s*월", " ", t)
    for v in co_variants(company) | {re.sub(r"\(주\)|㈜|주식회사|\s", "", (company or "").split("→")[0])}:
        if len(v) >= 2:
            t = re.sub(re.escape(v), " ", t, flags=re.I)
    words = re.findall(r"[가-힣]+|[a-z]+|\d+", t.lower())
    out = set()
    for w in words:
        w = re.sub(r"(공장|사업장|사업소|팀|실|부|파트)$", "", w) or w
        if w and w not in STOP and len(w) >= 2:
            out.add(w)
    return out


def fields(title):
    t = (title or "").lower()
    return {f for f in FIELD if f in t}


def covered(a, b):
    """a 의 모든 낱말이 b 의 어떤 낱말과 같거나 앞부분이 겹친다('인사총무' ~ '인사총무사무원')"""
    return all(any(x == y or x.startswith(y) or y.startswith(x) for y in b) for x in a)


def same_posting(a, b):
    if not same_company(a["company"], b["company"]):
        return False
    if fields(a["title"]) != fields(b["title"]):
        return False   # 안전관리 ≠ 보건관리, 인재 ≠ 그룹장
    ta, tb = tokens(a["title"], a["company"]), tokens(b["title"], b["company"])
    if not ta and not tb:
        return title_core(a["title"], a["company"]) == title_core(b["title"], b["company"])
    return (covered(ta, tb) or covered(tb, ta)) and bool(ta) and bool(tb)


def merge_duplicates(jobs):
    """jobs(list of dict) → (합친 목록, 합친 수). 같은 상태(진행/마감)끼리만 합친다."""
    parent = list(range(len(jobs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cores = [title_core(j["title"], j["company"]) for j in jobs]
    for i in range(len(jobs)):
        for k in range(i + 1, len(jobs)):
            if jobs[i]["status"] != jobs[k]["status"]:
                continue
            if (same_title(cores[i], cores[k]) and same_company(jobs[i]["company"], jobs[k]["company"])) or same_posting(jobs[i], jobs[k]):
                parent[find(i)] = find(k)
    groups = {}
    for i in range(len(jobs)):
        groups.setdefault(find(i), []).append(jobs[i])
    out, merged = [], 0
    for g in groups.values():
        if len(g) == 1:
            out.append(g[0]); continue
        g.sort(key=score, reverse=True)
        rep = dict(g[0])
        rep["aliases"] = [j["id"] for j in g[1:]]
        seen_links = {rep.get("link")}
        rep["also"] = []
        for j in g[1:]:
            if j.get("link") and j["link"] not in seen_links:
                rep["also"].append({"site": site_of(j["link"]), "link": j["link"]})
                seen_links.add(j["link"])
        firsts = [j["firstSeen"] for j in g if j.get("firstSeen")]
        rep["firstSeen"] = min(firsts) if firsts else rep.get("firstSeen", "")
        if not re.match(r"\d{4}-", rep.get("deadline") or ""):
            rep["deadline"] = next((j["deadline"] for j in g if re.match(r"\d{4}-", j.get("deadline") or "")), rep.get("deadline", ""))
        rep["sec"] = sorted({s for j in g for s in j["sec"]}, key=["환경", "안전"].index)
        out.append(rep)
        merged += len(g) - 1
    return out, merged
