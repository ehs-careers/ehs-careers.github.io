# -*- coding: utf-8 -*-
"""직무 관련성 판정: 규칙 v2(넓은 그물) + 학습 모델(오탐 제거). job_radar.py 와 실험 코드가 함께 쓴다."""
import re


def text(r):
    return f"{r['title']} || {r.get('company', '')}"


def build_model():
    # scikit-learn 은 여기서만 불러온다. rule_v2(규칙 판정)는 scikit-learn 없이도 돌아야 하기 때문
    # (미설치이거나 스마트 앱 컨트롤이 DLL을 막으면 job_radar.py 가 규칙만으로 진행한다).
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline, make_union
    from sklearn.linear_model import LogisticRegression
    feats = make_union(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 4), sublinear_tf=True, min_df=2),
        TfidfVectorizer(analyzer="word", token_pattern=r"[가-힣A-Za-z]{2,}", ngram_range=(1, 2), sublinear_tf=True))
    return make_pipeline(feats, LogisticRegression(C=8, class_weight="balanced", max_iter=4000))


_cache = {}
def _re(s, key):
    if key not in _cache:
        _cache[key] = re.compile(s[key], re.I)
    return _cache[key]


def rule_v2(r, s):
    return bool(_re(s, "rule_v2_include").search(r["title"])) and not _re(s, "rule_v2_exclude").search(r["title"] + " " + r.get("company", ""))


# ---------------------------------------------------------------- 분야(환경 / 안전) — settings.profiles, 요구사항 원문은 docs/profiles_spec.md
def _pre(p, key):
    k = (id(p), key)
    if k not in _cache:
        _cache[k] = re.compile(p[key], re.I) if p.get(key) else None
    return _cache[k]


def _profile_ok(name, p, r, s, rank):
    title, both = r["title"], r["title"] + " " + r.get("company", "")
    if p.get("base_rule") and not rule_v2(r, s):  # 환경: 기존 규칙 v2 를 그대로 통과해야 함
        return False
    inc, exc = _pre(p, "include"), _pre(p, "exclude")
    if inc and not inc.search(title):
        return False
    if exc:
        text = both
        rf, ri = _pre(p, "rescue_from"), _pre(p, "rescue_if")
        if rf and ri and ri.search(title):   # '산업안전&소방안전' 겸직: 소방 때문에 버리지 않는다
            text = rf.sub(" ", text)
        if exc.search(text):
            return False
    gate = _pre(p, "construction")
    if gate and gate.search(both):  # 건설: 대기업 건설사의 본사·사무직만 (현장 채용 제외)
        if rank is not None and rank > int(p.get("construction_max_rank", 2)):
            return False
        site = _pre(p, "construction_site")
        if site and site.search(title):
            return False
    return True


def _profiles(s):
    return [(n, p) for n, p in (s.get("profiles") or {}).items() if isinstance(p, dict)]  # '_설명' 같은 글 항목은 건너뜀


def sections_loose(r, s):
    """회사 규모를 모를 때의 1차 거르기: 어느 분야든 통과 가능성이 있으면 True."""
    return any(_profile_ok(n, p, r, s, None) for n, p in _profiles(s)) if _profiles(s) else rule_v2(r, s)


def sections(r, s, rank=None):
    """이 공고가 들어갈 분야 목록. profiles 설정이 없으면 예전처럼 규칙 v2 하나(['환경'])."""
    if not _profiles(s):
        return ["환경"] if rule_v2(r, s) else []
    return [n for n, p in _profiles(s) if _profile_ok(n, p, r, s, rank)]


# ---------------------------------------------------------------- 배포용: 학습 데이터 + 내 피드백으로 학습
import json as _json
from pathlib import Path as _Path

ML_DIR = _Path(__file__).resolve().parent
USER_LABELS = ML_DIR / "user_labels.json"
_model = None


def load_user_labels():
    try:
        return _json.loads(USER_LABELS.read_text(encoding="utf-8"))
    except Exception:
        return []


def merge_feedback(paths):
    """리포트에서 내보낸 radar_feedback*.json 을 user_labels.json 에 합친다. 같은 공고는 최신 표시로 덮어쓴다."""
    cur = {f"{r['company']}|{r['title']}": r for r in load_user_labels()}
    n = 0
    for p in paths:
        try:
            for r in _json.loads(_Path(p).read_text(encoding="utf-8")):
                if r.get("label") in (0, 1) and r.get("title"):
                    cur[f"{r.get('company', '')}|{r['title']}"] = {"title": r["title"], "company": r.get("company", ""), "label": int(r["label"])}
                    n += 1
        except Exception as e:
            print("  피드백 파일 읽기 실패:", p, e)
    USER_LABELS.write_text(_json.dumps(list(cur.values()), ensure_ascii=False, indent=0), encoding="utf-8")
    return n


def get_model():
    """기본 라벨 1,190건 + 내 피드백(3배 가중)으로 학습. 실행마다 수 초."""
    global _model
    if _model is None:
        base = _json.loads((ML_DIR / "labeled_data.json").read_text(encoding="utf-8"))
        user = load_user_labels()
        rows = base + user * 3
        _model = build_model().fit([text(r) for r in rows], [r["label"] for r in rows])
        print(f"  관련성 모델 학습: 기본 {len(base)}건 + 내 피드백 {len(user)}건")
    return _model


def score(items):
    return list(get_model().predict_proba([text(r) for r in items])[:, 1]) if items else []
