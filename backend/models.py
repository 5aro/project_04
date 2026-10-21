from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

CRITERIA = ["문제의 명확성", "시장 수요", "기술 차별성", "구현 가능성", "수익화 가능성"]

class Idea(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    idea: str = Field(min_length=10, max_length=6000)

class AnalysisRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    idea: str = Field(min_length=10, max_length=6000)
    problem: str | None = Field(default=None, min_length=2, max_length=1500)
    technology: str | None = Field(default=None, min_length=2, max_length=1500)
    customers: str | None = Field(default=None, min_length=2, max_length=1500)
    difference: str | None = Field(default=None, min_length=2, max_length=1500)
    revenue: str | None = Field(default=None, min_length=2, max_length=1500)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    missing_information: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def all_fields_or_none(self):
        values=[self.problem,self.technology,self.customers,self.difference,self.revenue]
        if any(values) and not all(values):
            raise ValueError('Provide all five structured fields or omit all of them')
        return self

class InputFact(BaseModel):
    value: str
    quote: str = ""
    basis: Literal["원문", "AI 가설", "사용자 확인"] = "원문"

class ParsedIdea(BaseModel):
    title: str
    problem: InputFact | None
    technology: InputFact | None
    customers: InputFact | None
    difference: InputFact | None
    revenue: InputFact | None
    keywords: list[str] = Field(min_length=1, max_length=8)
    patent_queries: list[str] = Field(min_length=1, max_length=3)
    market_query: str
    assumptions: list[str]
    missing_information: list[str]

class Patent(BaseModel):
    id: str
    title: str
    publication_number: str | None
    abstract: str = Field(min_length=10)
    description: str = ""
    claims: str = ""
    url: HttpUrl | None
    source: str
    is_demo: bool = False
    application_number: str = ""
    applicant: str = ""
    publication_date: str = ""
    ipc: str = ""
    link_label: str = "원문 확인"

class Evidence(BaseModel):
    citation_id: int
    patent: Patent
    section: str
    quote: str
    similarity: float
    claim_number: int | None = None
    parent_claim_numbers: list[int] = Field(default_factory=list)
    retrieval_source: str = "특허 검색"

class PatentFact(BaseModel):
    value: str
    quote: str = Field(min_length=1)
    section: Literal["abstract", "claims"]

class PatentData(BaseModel):
    citation_id: int
    problem: PatentFact | None
    technology: PatentFact | None
    application: PatentFact | None
    effect: PatentFact | None
    limitations: list[str]

class PatentDataset(BaseModel):
    patents: list[PatentData]

class MarketSource(BaseModel):
    title: str
    url: HttpUrl

class MarketEvidence(BaseModel):
    id: str
    summary: str
    sources: list[MarketSource] = Field(min_length=1)
    retrieved_at: str

class MarketResult(BaseModel):
    status: Literal["completed", "no_results", "failed", "demo", "disabled"]
    evidence: list[MarketEvidence]
    warning: str

class Comparison(BaseModel):
    citation_id: int
    similarity: str
    difference: str
    uncertainty: str

class Evaluation(BaseModel):
    criterion: Literal["문제의 명확성", "시장 수요", "기술 차별성", "구현 가능성", "수익화 가능성"]
    score: int | None = Field(ge=0, le=5, strict=True)
    reason: str
    evidence: str
    source_ids: list[str]

class Generated(BaseModel):
    summary: str
    comparisons: list[Comparison]
    evaluations: list[Evaluation]
    improvements: list[str] = Field(min_length=3, max_length=3)

    @field_validator("evaluations")
    @classmethod
    def all_criteria(cls, value):
        if sorted(x.criterion for x in value) != sorted(CRITERIA):
            raise ValueError("Each criterion must occur exactly once")
        return value

class PatentReviewItem(BaseModel):
    citation_id: int
    similarity: float
    review_priority: Literal["상세 검토 우선", "비교 권장", "참고"]
    claims_available: bool
    matched_section: str

class PatentReview(BaseModel):
    status: Literal["유사 문헌 상세 검토 권장", "검색 결과 비교", "평가 보류"]
    summary: str
    items: list[PatentReviewItem]
    limitations: list[str]

class Report(BaseModel):
    mode: Literal["demo", "rag"]
    status: Literal["completed", "partial", "no_results"]
    scope: str
    parsed_idea: ParsedIdea
    patent_data: list[PatentData]
    market: MarketResult
    generated: Generated
    business_plan_draft: str
    patent_review: PatentReview
    evidence: list[Evidence]
    score: float | None
    evaluated_count: int
    score_label: str
    warnings: list[str]
    metadata: dict[str, str]
