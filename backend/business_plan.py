from .models import ParsedIdea, Generated, PatentData, Evidence, MarketResult, PatentReview


def _fact(fact):
    if fact is None or not fact.value.strip():
        return "추가 입력 필요"
    return f"{fact.value} _(근거: {fact.basis})_"


def _linked_sources(ids, evidence, market):
    links = []
    for source_id in ids:
        if source_id == "I":
            links.append("[I] 사용자 입력")
        elif source_id.startswith("P"):
            try:
                citation_id = int(source_id[1:])
            except ValueError:
                continue
            item = next((row for row in evidence if row.citation_id == citation_id), None)
            if item:
                url = str(item.patent.url) if item.patent.url else ""
                label = f"[{source_id}] {item.patent.title}"
                links.append(f"[{label}]({url})" if url else label)
        elif source_id.startswith("M"):
            item = next((row for row in market.evidence if row.id == source_id), None)
            if item:
                links.append(f"[{source_id}] 시장 근거")
    return ", ".join(links) if links else "연결된 출처 없음"


def build_business_plan(parsed: ParsedIdea, generated: Generated, patent_data: list[PatentData],
                        market: MarketResult, evidence: list[Evidence], patent_review: PatentReview,
                        score: float | None, evaluated_count: int, score_label: str) -> str:
    """Assemble an editable report draft from already validated analysis; do not make another model call."""
    lines = [
        f"# {parsed.title} | 사업기획 보고서 초안",
        "",
        "> 초기 검토용 초안입니다. 출처가 연결된 자료와 AI 가설을 구분했으며, 법률·투자 판단을 대신하지 않습니다.",
        "",
        "## 1. 핵심 요약",
        "",
        generated.summary,
        "",
        f"- 초기 검토 지표: {score_label}" + (f" {score}/100" if score is not None else " (평가 보류)"),
        f"- 근거 확인 범위: 5개 평가 항목 중 {evaluated_count}개",
        "",
        "## 2. 문제와 고객",
        "",
        f"- 해결 과제: {_fact(parsed.problem)}",
        f"- 대상 고객: {_fact(parsed.customers)}",
        "",
        "## 3. 제품·기술과 사업모델 가설",
        "",
        f"- 핵심 기술: {_fact(parsed.technology)}",
        f"- 제안된 차별점: {_fact(parsed.difference)}",
        f"- 수익모델 가설: {_fact(parsed.revenue)}",
        "",
        "## 4. 선행특허 검토",
        "",
    ]
    if evidence:
        lines.append(f"검색 결과 {len(evidence)}건을 비교했습니다. 상태: {patent_review.status}.")
        lines.append("")
        comparisons = {row.citation_id: row for row in generated.comparisons}
        structured_patents = {row.citation_id: row for row in patent_data}
        for item in evidence:
            citation = f"P{item.citation_id}"
            url = str(item.patent.url) if item.patent.url else ""
            title = f"[{citation}] {item.patent.title}"
            if url:
                title = f"[{title}]({url})"
            comparison = comparisons.get(item.citation_id)
            lines.extend([f"### {title}", f"- 공개번호: {item.patent.publication_number or '정보 없음'} · 청구항 원문: {'확보' if item.patent.claims.strip() else '미확보'}"])
            if comparison:
                lines.extend([f"- 유사 구성: {comparison.similarity}", f"- 구별되는 구성: {comparison.difference}", f"- 불확실성: {comparison.uncertainty}"])
            structured = structured_patents.get(item.citation_id)
            if structured:
                patent_facts = [(label, getattr(structured, key)) for key, label in
                    (("problem", "문서에 기재된 과제"), ("technology", "문서에 기재된 기술"),
                     ("application", "문서에 기재된 적용 분야"), ("effect", "문서에 기재된 효과"))]
                for label, fact in patent_facts:
                    if fact:
                        lines.append(f"- {label}: {fact.value} (인용 위치: {fact.section}; 원문: “{fact.quote}”)")
            lines.append(f"- 검색 근거 위치: {item.section}; 유사도 {item.similarity:.3f}는 검색 정렬 참고값입니다.")
            lines.append("")
        lines.append(patent_review.summary)
    else:
        lines.extend(["관련성 기준을 충족하는 특허 근거를 찾지 못해 비교를 보류합니다. 이는 관련 특허가 없다는 결론이 아닙니다.", ""])

    lines.extend(["", "## 5. 시장·경쟁 근거", ""])
    market_rows = [row for row in generated.evaluations if row.criterion in {"시장 수요", "수익화 가능성"}]
    for row in market_rows:
        lines.append(f"- **{row.criterion}:** {row.reason} 근거: {row.evidence}. 출처: {_linked_sources(row.source_ids, evidence, market)}.")
    if market.evidence:
        lines.extend(["", "### 확인된 공개 자료", ""])
        for item in market.evidence:
            lines.append(f"- **[{item.id}]** {item.summary}")
            for source in item.sources:
                lines.append(f"  - [{source.title}]({source.url}) · 조회 {item.retrieved_at}")
    else:
        lines.extend(["", "출처가 연결된 시장 자료가 없습니다. 시장 수요와 수익화 결론은 보류하고 고객 인터뷰·파일럿으로 확인해야 합니다."])

    lines.extend(["", "## 6. 초기 검토 지표", "", "| 평가 항목 | 점수 | 근거 요약 | 출처 |", "|---|---:|---|---|"])
    for row in generated.evaluations:
        value = "보류" if row.score is None else f"{row.score}/5"
        reason = " ".join(f"{row.reason} {row.evidence}".split()).replace("|", "\\|")
        lines.append(f"| {row.criterion} | {value} | {reason} | {_linked_sources(row.source_ids, evidence, market)} |")
    lines.extend(["", "점수는 입력과 확보된 자료에 따른 초기 검토 지표이며 성공 확률, 투자 판단 또는 특허 등록 가능성 평가가 아닙니다."])

    lines.extend(["", "## 7. 가정과 미확인 사항", ""])
    assumptions = parsed.assumptions or []
    missing = parsed.missing_information or []
    for row in assumptions:
        lines.append(f"- **가정:** {row}")
    for row in missing:
        lines.append(f"- **확인 필요:** {row}")
    if not assumptions and not missing:
        lines.append("- 추가 가정이나 확인 과제가 구조화되지 않았습니다. 고객·성능·가격 근거를 별도로 검토하세요.")
    if not market.evidence:
        lines.append("- **시장 자료 공백:** 실제 구매자, 지불 의사, 제품별 가격·운영 비용 근거가 필요합니다.")
    if not evidence:
        lines.append("- **특허 자료 공백:** 검색 범위와 청구항 원문을 보강한 선행기술 조사가 필요합니다.")

    lines.extend(["", "## 8. 우선순위별 다음 검증 과제", ""])
    for index, action in enumerate(generated.improvements, start=1):
        lines.append(f"{index}. {action}")
    lines.extend(["", "## 참고 자료", ""])
    for item in evidence:
        url = str(item.patent.url) if item.patent.url else "원문 링크 없음"
        lines.append(f"- **P{item.citation_id}** {item.patent.title} · {item.patent.publication_number or '공개번호 없음'} · {url}")
    for item in market.evidence:
        for source in item.sources:
            lines.append(f"- **{item.id}** {source.title} · {source.url} · 조회 {item.retrieved_at}")
    if not evidence and not market.evidence:
        lines.append("- 연결된 외부 자료가 없습니다.")
    lines.extend(["", "---", "이 초안은 이용 가능한 검색 코퍼스와 웹 자료에 한정됩니다. 특허 등록 가능성·침해 여부는 청구항 단위의 전문가 검토가 필요하며, 시장 수요와 지불 의사는 실제 고객 검증이 필요합니다."])
    return "\n".join(lines)
