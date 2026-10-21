from urllib.parse import urlparse
from .models import Generated, Comparison

def _evidence_caps(patent_data, market):
    """Cap model scores at what this MVP can verify; idea plausibility is not validation."""
    market_domains={urlparse(str(source.url)).netloc.lower() for item in market.evidence for source in item.sources}
    claim_evidence=any(getattr(p,field) and getattr(p,field).section=="claims"
        for p in patent_data for field in ("problem","technology","application","effect"))
    return {
        "문제의 명확성":(3,"사용자 설명만 확인했으며 고객 인터뷰·문제 빈도 검증은 없습니다."),
        "시장 수요":(min(3, 2 if len(market_domains)<2 else 3),"공개 웹 자료는 실제 목표 고객의 수요 검증을 대체하지 않습니다."),
        "기술 차별성":(3 if claim_evidence else 1,"청구항 원문을 데이터화해 비교하지 못하면 차별성 점수를 제한합니다."),
        "구현 가능성":(2,"시제품·성능 시험·원가 검증 자료가 없어 구현 가능성 점수를 제한합니다."),
        "수익화 가능성":(2,"아이디어별 지불 의사나 구매 검증 자료가 없어 수익화 점수를 제한합니다."),
    }

def enforce_and_score(parsed, result: Generated, patent_data, market, demo=False):
    patent_ids = {p.citation_id for p in patent_data}
    comparisons = {}
    for row in result.comparisons:
        if row.citation_id in patent_ids and row.citation_id not in comparisons:
            comparisons[row.citation_id] = row
    # Keep the report usable when the model omits a comparison or invents an ID.
    result.comparisons = [comparisons.get(i) or Comparison(
        citation_id=i,
        similarity="비교 결과가 제공되지 않아 평가를 보류합니다.",
        difference="아이디어와 특허 원문을 직접 대조해야 합니다.",
        uncertainty="구조화 응답에 비교 내용이 없습니다.") for i in sorted(patent_ids)]
    p_refs = {f"P{p.citation_id}" for p in patent_data if any(
        getattr(p, key) for key in ("problem","technology","application","effect"))}
    m_refs = {m.id for m in market.evidence}
    valid = p_refs | m_refs | {"I"}
    caps=_evidence_caps(patent_data,market)
    for row in result.evaluations:
        refs = set(row.source_ids)
        invalid_refs = not refs.issubset(valid)
        missing = (demo or not refs or invalid_refs
            or (row.criterion == "문제의 명확성" and (parsed.problem is None or "I" not in refs))
            or (row.criterion in {"시장 수요","수익화 가능성"} and not refs.intersection(m_refs))
            or (row.criterion in {"기술 차별성","구현 가능성"} and not refs.intersection(p_refs)))
        if missing:
            row.score = None
            row.reason = ("평가 보류: 응답의 출처 번호를 확인할 수 없습니다." if invalid_refs else
                "평가 보류: 이 항목에 필요한 입력 또는 출처가 연결된 근거가 부족합니다.")
            row.source_ids = []
        if row.score is not None and not row.evidence.strip():
            raise ValueError("Scored item requires evidence")
        if row.score is not None:
            cap,reason=caps[row.criterion]
            if row.score>cap:
                row.score=cap
                row.reason=f"{row.reason} 근거 상한 적용({cap}/5): {reason}"
    scores = [e.score for e in result.evaluations if e.score is not None]
    count = len(scores)
    # Use the full 25-point rubric denominator so a small evaluated subset cannot
    # look like a strong overall result. Withheld items remain visibly unassessed.
    return (round(sum(scores) / 25 * 100, 1) if count else None,
            count, "종합 점수" if count == 5 else "잠정 점수" if count else "평가 보류")
