"""Offline KIPRIS patent corpus ingestion.

Run from the repository root: python indexing.py --query "전기차 배터리 급속 충전"
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from backend.kipris import KiprisClient
from backend.retrieval import persist_patent_index

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/".env")

def parse_args():
    parser=argparse.ArgumentParser(description="KIPRIS 특허를 청킹·임베딩해 재사용 가능한 ChromaDB에 적재합니다.")
    parser.add_argument("--query",action="append",default=[],help="특허 수집 검색어 (반복 지정 가능)")
    parser.add_argument("--query-file",type=Path,help="한 줄에 한 검색어를 적은 UTF-8 파일")
    parser.add_argument("--index-path",default=os.getenv("PATENT_INDEX_PATH","data/chroma"))
    parser.add_argument("--collection",default=os.getenv("PATENT_INDEX_COLLECTION","idealens_patents_v1"))
    parser.add_argument("--batch-size",type=int,default=3,help="KIPRIS 검색어 묶음 크기 (최대 3)")
    return parser.parse_args()

def main():
    args=parse_args()
    queries=list(args.query)
    if args.query_file:
        queries.extend(line.strip() for line in args.query_file.read_text(encoding="utf-8").splitlines()
                       if line.strip() and not line.lstrip().startswith("#"))
    queries=list(dict.fromkeys(q.strip() for q in queries if q.strip()))
    if not queries:
        raise SystemExit("검색어가 없습니다. --query 또는 --query-file을 지정하세요.")
    if not 1<=args.batch_size<=3:
        raise SystemExit("--batch-size는 1~3이어야 합니다.")
    kipris_key=os.getenv("KIPRIS_API_KEY","")
    openai_key=os.getenv("OPENAI_API_KEY","")
    if not kipris_key: raise SystemExit("기존 .env에 KIPRIS_API_KEY를 설정하세요.")
    if not openai_key: raise SystemExit("기존 .env에 OPENAI_API_KEY를 설정하세요.")
    kipris=KiprisClient(kipris_key,os.getenv("KIPRIS_API_PROFILE","access_key"))
    patents={}
    for start in range(0,len(queries),args.batch_size):
        group=queries[start:start+args.batch_size]
        rows=kipris.search(group)
        rows=[kipris.enrich(row) for row in rows]
        patents.update({row.id:row for row in rows})
        print(f"KIPRIS 검색어 {start+1}~{start+len(group)} 처리: 후보 {len(rows)}건, 누적 {len(patents)}건")
    if not patents: raise SystemExit("색인할 특허를 찾지 못했습니다. 검색어·KIPRIS 응답을 확인하세요.")
    model=os.getenv("EMBEDDING_MODEL","text-embedding-3-small")
    with OpenAI(timeout=40,max_retries=2) as client:
        result=persist_patent_index(list(patents.values()),client,model,args.index_path,args.collection,queries)
    claim_count=result["claims"]
    print(f"색인 완료: 특허 {result['patents']}건 / 청크 {result['chunks']}개 / 이번 실행 신규·갱신 {result['new_patents']}건")
    print(f"본문 보유: 청구항 {claim_count}건, 상세설명 {result['descriptions']}건")
    print(f"저장 위치: {result['path']} · 컬렉션: {args.collection}")

if __name__=="__main__":
    main()
