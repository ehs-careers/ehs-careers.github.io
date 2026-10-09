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


def merge_duplicates(jobs):
    """jobs(list of dict) → (합친 목록, 합친 수). 같은 상태(진행/마감)끼리만 합친다."""
    parent = list(range(len(jobs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cores = [title_core(j["title"], j["company"]) for j in jobs]
    by_len = {}
    for i, c in enumerate(cores):
        by_len.setdefault(c[:10], []).append(i)
    for idxs in by_len.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                i, k = idxs[x], idxs[y]
                if jobs[i]["status"] == jobs[k]["status"] and same_title(cores[i], cores[k]) and same_company(jobs[i]["company"], jobs[k]["company"]):
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
