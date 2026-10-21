import hashlib
import json
import math
import re
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from .models import Patent, Evidence

# Deterministic offline demonstration only; not a semantic embedding model.
def tokens(text):
    return re.findall(r"[가-힣a-zA-Z0-9]{2,}", text.lower())

def demo_embedding(text):
    vector = [0.0] * 512
    for word in tokens(text):
        for n in (2, 3):
            for i in range(max(0, len(word) - n + 1)):
                key = word[i:i+n]
                index = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 512
                vector[index] += 1
    norm = math.sqrt(sum(x*x for x in vector)) or 1
    return [x / norm for x in vector]

def load_patents(path, demo):
    patents = [Patent.model_validate(p) for p in json.loads(Path(path).read_text())]
    if not demo and any(p.is_demo or not p.url or not p.publication_number for p in patents):
        raise ValueError("Real data requires source links and publication numbers; demo records are forbidden")
    if len({p.id for p in patents}) != len(patents):
        raise ValueError("Duplicate patent IDs")
    return patents

@dataclass(frozen=True)
class PatentChunk:
    patent: Patent
    section: str
    document: str
    quote: str
    claim_number: int | None = None
    parent_claim_numbers: tuple[int, ...] = ()

def _recursive_chunks(text, size=800, overlap=150):
    """Split detailed description hierarchically while retaining bounded overlap."""
    text=re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        return []
    separators=["\n\n", "\n", "다. ", ". ", "; ", " "]
    result=[]; start=0
    while start<len(text):
        end=min(start+size,len(text))
        if end<len(text):
            lower=start+size//2
            cut=None
            for separator in separators:
                position=text.rfind(separator,lower,end)
                if position>=lower:
                    cut=position+len(separator)
                    break
            if cut is not None: end=cut
        result.append(text[start:end].strip())
        if end>=len(text): break
        start=max(start+1,end-overlap)
    return [part for part in result if part]

_CLAIM_HEADER=re.compile(r"(?im)^\s*(?:\[\s*청구항\s*(\d+)\s*\]|청구항\s*(\d+)\s*[:.)]?|제\s*(\d+)\s*항(?=\s*[:.)])|(\d+)\s*[.)])\s*")
_PARENT_CLAIM=re.compile(r"(?:제\s*(\d+)\s*항|청구항\s*(\d+))\s*에\s*있어서")

def _claim_records(text):
    """Parse numbered claims; retain parent references for dependent-claim context."""
    text=re.sub(r"\r\n?", "\n", text or "").strip()
    if not text:
        return []
    matches=list(_CLAIM_HEADER.finditer(text))
    claims=[]
    if not matches:
        return [(None,(),text)]
    for index,match in enumerate(matches):
        number=next((int(x) for x in match.groups() if x),None)
        end=matches[index+1].start() if index+1<len(matches) else len(text)
        body=text[match.end():end].strip()
        if not body: continue
        parents=tuple(dict.fromkeys(int(x) for x in _PARENT_CLAIM.findall(body) for x in x if x))
        claims.append((number,parents,body))
    if not claims:
        return [(None,(),text)]
    return claims

def chunks(patents):
    records=[]
    for patent in patents:
        # Invention title is metadata; the abstract remains one semantic unit.
        if patent.abstract.strip():
            records.append(PatentChunk(patent,"초록",patent.abstract,patent.abstract))
        # Detailed description is split recursively around paragraph/sentence boundaries.
        for index,part in enumerate(_recursive_chunks(patent.description)):
            records.append(PatentChunk(patent,"상세한 설명",part,part))
        # Claims are independent units. A dependent claim embeds its referenced parent
        # claim as context, while its evidence quote stays limited to the dependent claim.
        claims=_claim_records(patent.claims)
        by_number={number:body for number,_,body in claims if number is not None}
        for number,parents,body in claims:
            parent_context=" ".join(by_number[p] for p in parents if p in by_number)
            document=(f"상위 청구항: {parent_context}\n종속 청구항: {body}" if parent_context else body)
            section=f"청구항 {number}" if number is not None else "청구항"
            records.append(PatentChunk(patent,section,document,body,number,parents))
    return records

class Retriever:
    def __init__(self, patents, demo, client, embedding_model, minimum,retrieval_source="특허 검색"):
        import chromadb
        self.rows = chunks(patents)
        self.demo, self.client, self.model, self.minimum = demo, client, embedding_model, minimum
        self.retrieval_source=retrieval_source
        # Ephemeral corpus: no user queries are stored; avoids stale model/index mismatch.
        self.db = chromadb.EphemeralClient()
        import uuid
        self.collection = self.db.create_collection("patents-" + uuid.uuid4().hex, metadata={"hnsw:space": "cosine"})
        if self.rows:
            documents=[row.document for row in self.rows]
            vectors=[]
            for i in range(0,len(documents),64):
                vectors.extend(self.embed(documents[i:i+64]))
            self.collection.add(ids=[str(i) for i in range(len(documents))],documents=documents,embeddings=vectors,
                metadatas=[{"patent_id":row.patent.id,"title":row.patent.title,"section":row.section,
                    "claim_number":row.claim_number if row.claim_number is not None else -1,
                    "parent_claim_numbers":",".join(map(str,row.parent_claim_numbers))} for row in self.rows])

    def embed(self, texts):
        if self.demo:
            return [demo_embedding(t) for t in texts]
        result = self.client.embeddings.create(model=self.model, input=texts)
        return [x.embedding for x in sorted(result.data, key=lambda x: x.index)]

    def search(self, query):
        if not self.rows:
            return []
        result = self.collection.query(query_embeddings=self.embed([query]), n_results=len(self.rows))
        found, seen = [], set()
        for key, distance in zip(result["ids"][0], result["distances"][0]):
            row=self.rows[int(key)]
            relevance = 1 - distance
            if relevance < self.minimum or row.patent.id in seen:
                continue
            seen.add(row.patent.id)
            found.append(Evidence(citation_id=len(found)+1,patent=row.patent,section=row.section,
                quote=row.quote,similarity=round(relevance,3),claim_number=row.claim_number,
                parent_claim_numbers=list(row.parent_claim_numbers),retrieval_source=self.retrieval_source))
            if len(found) == 5:
                break
        return found

    def close(self):
        self.db.delete_collection(self.collection.name)

CHUNK_VERSION="patent-chunks-v2"

def chunk_id(row):
    identity="|".join((row.patent.id,row.section,str(row.claim_number or ""),row.document))
    return hashlib.sha256(identity.encode()).hexdigest()

def _metadata(row):
    return {"patent_id":row.patent.id,"title":row.patent.title,"section":row.section,
        "claim_number":row.claim_number if row.claim_number is not None else -1,
        "parent_claim_numbers":",".join(map(str,row.parent_claim_numbers))}

def _embed(client, model, documents, batch_size=64):
    vectors=[]
    for start in range(0,len(documents),batch_size):
        response=client.embeddings.create(model=model,input=documents[start:start+batch_size])
        vectors.extend(item.embedding for item in sorted(response.data,key=lambda item:item.index))
    return vectors

def persist_patent_index(patents, client, model, persist_path, collection_name, queries, collection=None):
    """Upsert patent chunks in a persistent Chroma collection and atomically save source records."""
    import chromadb
    path=Path(persist_path); path.mkdir(parents=True,exist_ok=True)
    db=None
    if collection is None:
        db=chromadb.PersistentClient(path=str(path))
        try:
            collection=db.get_collection(collection_name)
        except Exception:
            collection=db.create_collection(collection_name,metadata={"hnsw:space":"cosine","embedding_model":model,
                "chunk_version":CHUNK_VERSION,"source":"KIPRIS Plus"})
        metadata=collection.metadata or {}
        if metadata.get("embedding_model")!=model or metadata.get("chunk_version")!=CHUNK_VERSION:
            raise ValueError("기존 인덱스와 임베딩 모델 또는 청킹 버전이 다릅니다. 별도 collection 이름을 사용하세요.")
    else:
        metadata=collection.metadata or {}
        if metadata.get("embedding_model")!=model or metadata.get("chunk_version")!=CHUNK_VERSION:
            raise ValueError("기존 인덱스와 임베딩 모델 또는 청킹 버전이 다릅니다. 별도 collection 이름을 사용하세요.")
    patents_file=path/f"{collection_name}.patents.json"
    existing={}
    if patents_file.exists():
        existing={p["id"]:Patent.model_validate(p) for p in json.loads(patents_file.read_text(encoding="utf-8"))}
    incoming={p.id:p for p in patents}
    for patent_id,new in list(incoming.items()):
        old=existing.get(patent_id)
        if old:
            incoming[patent_id]=new.model_copy(update={field:(getattr(new,field) or getattr(old,field))
                for field in ("abstract","description","claims","applicant","publication_number","publication_date","ipc")})
    all_patents={**existing,**incoming}
    rows=chunks(list(all_patents.values()))
    incoming_ids=set(incoming)
    if incoming_ids:
        new_rows=[row for row in rows if row.patent.id in incoming_ids]
        prepared=[]
        for start in range(0,len(new_rows),64):
            batch=new_rows[start:start+64]
            documents=[row.document for row in batch]
            prepared.append((batch,documents,_embed(client,model,documents)))
        # Embedding all incoming content first means a provider failure cannot erase old chunks.
        for patent_id in incoming_ids:
            old_chunks=collection.get(where={"patent_id":patent_id},include=[])
            if old_chunks["ids"]:collection.delete(ids=old_chunks["ids"])
        for batch,documents,vectors in prepared:
            collection.upsert(ids=[chunk_id(row) for row in batch],documents=documents,
                embeddings=vectors,metadatas=[_metadata(row) for row in batch])
    temp=patents_file.with_suffix(".json.tmp")
    temp.write_text(json.dumps([p.model_dump(mode="json") for p in all_patents.values()],ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(patents_file)
    previous=collection.metadata or {}
    collection.modify(metadata={**{key:value for key,value in previous.items() if key!="hnsw:space"},"embedding_model":model,
        "chunk_version":CHUNK_VERSION,"source":"KIPRIS Plus",
        "indexed_at":datetime.now(timezone.utc).isoformat(),
        "queries":json.dumps(list(dict.fromkeys([*json.loads(previous.get("queries","[]")),*queries])),ensure_ascii=False)})
    return {"patents":len(all_patents),"chunks":collection.count(),"new_patents":len(incoming),
        "claims":sum(bool(p.claims) for p in all_patents.values()),
        "descriptions":sum(bool(p.description) for p in all_patents.values()),"path":str(path)}

class PersistentPatentRetriever:
    def __init__(self,persist_path,collection_name,client,embedding_model,minimum):
        import chromadb
        self.path=Path(persist_path); self.client=client; self.model=embedding_model; self.minimum=minimum
        db=chromadb.PersistentClient(path=str(self.path))
        self.collection=db.get_collection(collection_name)
        metadata=self.collection.metadata or {}
        if metadata.get("embedding_model")!=embedding_model or metadata.get("chunk_version")!=CHUNK_VERSION:
            raise ValueError("영구 특허 인덱스의 임베딩 모델/청킹 버전이 현재 설정과 다릅니다.")
        patents_file=self.path/f"{collection_name}.patents.json"
        if not patents_file.exists(): raise ValueError("인덱스의 특허 원문 파일을 찾을 수 없습니다.")
        self.collection_name=collection_name
        self.refresh()

    def refresh(self):
        patents_file=self.path/f"{self.collection_name}.patents.json"
        patents=[Patent.model_validate(row) for row in json.loads(patents_file.read_text(encoding="utf-8"))]
        self.rows=chunks(patents)
        self.by_id={chunk_id(row):row for row in self.rows}
        self.metadata=self.collection.metadata or {}
        self.patent_ids={patent.id for patent in patents}

    def search(self,query):
        count=self.collection.count()
        if not count:return []
        vectors=_embed(self.client,self.model,[query])
        result=self.collection.query(query_embeddings=vectors,n_results=min(count,500),include=["distances"])
        found=[]; seen=set()
        for identifier,distance in zip(result["ids"][0],result["distances"][0]):
            row=self.by_id.get(identifier)
            if row is None:continue
            relevance=1-distance
            if relevance<self.minimum or row.patent.id in seen:continue
            seen.add(row.patent.id)
            found.append(Evidence(citation_id=len(found)+1,patent=row.patent,section=row.section,quote=row.quote,
                similarity=round(relevance,3),claim_number=row.claim_number,
                parent_claim_numbers=list(row.parent_claim_numbers),retrieval_source="기존 Chroma 색인"))
            if len(found)==5:break
        return found

    def close(self):
        # Persistent collections are intentionally retained between requests/restarts.
        return None
