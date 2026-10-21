"""KIPRIS Plus freeSearchInfo / legacy getWordSearch adapters.
See docs/DATA_SOURCES.md for official parameter references.
"""
import re
import xml.etree.ElementTree as ET
import httpx
from urllib.parse import urlencode
from .models import Patent

class KiprisError(RuntimeError):
    pass

def clean(value):
    return re.sub(r"<[^>]*>", "", value or "").strip()

def descendant_value(row,*names):
    wanted={name.lower() for name in names}
    for node in row.iter():
        if node.tag.rsplit('}',1)[-1].lower() in wanted:
            text=''.join(node.itertext()).strip()
            if text:return clean(text)
    return ''

def claim_text(root):
    rows=[]
    for node in root.iter():
        if node.tag.lower() not in {'claim','claiminfo','claimitem','claimdata'}:continue
        number=descendant_value(node,'claimNumber','claimNo','claimNum','claimSeq')
        body=descendant_value(node,'claimText','claimContent','claimDesc','claimContents')
        if not body:
            body=' '.join(''.join(node.itertext()).split())
        if body:
            rows.append(f"청구항 {number}\n{body}" if number and not re.search(rf"청구항\s*{re.escape(number)}",body) else body)
    rows=list(dict.fromkeys(rows))
    if rows:return '\n'.join(rows)
    # Some response profiles return one flat claims field instead of claim elements.
    return descendant_value(root,'claims','claimText','claimContent','claimDesc','claimContents')

def parse_response(content):
    if len(content) > 8_000_000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise KiprisError("KIPRIS 응답 형식을 확인해주세요.")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise KiprisError("KIPRIS가 XML 대신 다른 형식의 응답을 반환했습니다.") from None
    for node in root.iter():
        node.tag = node.tag.rsplit("}",1)[-1]
    code = root.findtext('.//resultCode')
    success = root.findtext('.//successYN')
    if (code and code.strip() not in {'00','0'}) or (success and success.strip().upper() == 'N'):
        raise KiprisError("KIPRIS 인증·상품 이용 권한 또는 요청 한도를 확인해주세요.")
    records = root.findall('.//PatentUtilityInfo') or root.findall('.//item')
    if not records and root.find('.//TotalSearchCount') is None and root.find('.//totalCount') is None and root.find('.//items') is None:
        raise KiprisError("KIPRIS 응답 구조를 인식하지 못했습니다. API 프로필을 확인해주세요.")
    results=[]
    for row in records:
        def value(*names):
            return descendant_value(row,*names)
        title=value('InventionName','inventionTitle')
        abstract=value('Abstract','astrtCont')
        number=value('ApplicationNumber','applicationNumber')
        if not title or not number or len(abstract)<10 or abstract in {'내용 없음','내용 없음.'}:
            continue
        result_url='https://www.kipris.or.kr/khome/search/searchResult.do?'+urlencode({'queryText':number})
        results.append(Patent(id=number,title=title,application_number=number,
            publication_number=value('OpeningNumber','openNumber') or None,
            abstract=abstract,
            description=value('TechnicalField','DetailedDescription','description','techField'),
            claims=claim_text(row),url=result_url,
            link_label='KIPRIS 검색 페이지 열기 · 번호 복사 후 검색', source='KIPRIS Plus',
            applicant=value('Applicant','applicantName'), publication_date=value('OpeningDate','openDate'),
            ipc=value('InternationalpatentclassificationNumber','ipcNumber')))
    return results

class KiprisClient:
    def __init__(self, key, profile='access_key', transport=None):
        self.key, self.profile, self.transport = key, profile, transport
        if profile not in {'access_key','service_key'}:
            raise ValueError('KIPRIS_API_PROFILE must be access_key or service_key')

    def search(self, queries):
        if not self.key:
            raise KiprisError("KIPRIS_API_KEY를 .env에 설정해주세요.")
        candidates={}
        with httpx.Client(timeout=25, transport=self.transport, follow_redirects=False) as client:
            for query in list(dict.fromkeys(queries))[:3]:
                # Only technical keyword queries are sent, never the complete idea.
                word=' '.join(re.findall(r'[가-힣a-zA-Z0-9]+', query))[:100]
                if not word:
                    continue
                if self.profile=='access_key':
                    path='openapi/rest/patUtiModInfoSearchSevice/freeSearchInfo'
                    params=dict(word=word,patent='true',utility='false',docsStart=1,docsCount=30,lastvalue='',accessKey=self.key)
                else:
                    path='kipo-api/kipi/patUtiModInfoSearchSevice/getWordSearch'
                    params=dict(word=word,year=0,patent='true',utility='false',pageNo=1,numOfRows=30,ServiceKey=self.key)
                try:
                    response=client.get('https://plus.kipris.or.kr/'+path, params=params)
                    response.raise_for_status()
                except httpx.HTTPError:
                    # Do not include the request URL: it contains the API key.
                    raise KiprisError('KIPRIS 연결에 실패했습니다. HTTPS 접근·키·이용 권한을 확인해주세요.') from None
                for patent in parse_response(response.content):
                    candidates[patent.id]=patent
        return list(candidates.values())

    def enrich(self, patent):
        """Request detailed bibliographic data for offline indexing; tolerate per-record gaps."""
        if not self.key:return patent
        profile=self.profile
        path=('openapi/rest/patUtiModInfoSearchSevice/getBibliographyDetailInfoSearch'
              if profile=='access_key' else
              'kipo-api/kipi/patUtiModInfoSearchSevice/getBibliographyDetailInfoSearch')
        params={'applicationNumber':patent.application_number}
        params['accessKey' if profile=='access_key' else 'ServiceKey']=self.key
        try:
            with httpx.Client(timeout=25,transport=self.transport,follow_redirects=False) as client:
                response=client.get('https://plus.kipris.or.kr/'+path,params=params)
                response.raise_for_status()
            content=response.content
            if len(content)>8_000_000 or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                return patent
            root=ET.fromstring(content)
            for node in root.iter(): node.tag=node.tag.rsplit('}',1)[-1]
            fields={}
            aliases={
                'abstract':{'abstract','astrtcont'},
                'description':{'description','detailedescription','inventiondescription','fulltext','fulltextcontent','techfield'},
                'applicant':{'applicant','applicantname'},
            }
            for field,names in aliases.items():
                values=[]
                for node in root.iter():
                    if node.tag.lower() in names:
                        value=' '.join(''.join(node.itertext()).split())
                        if value and value not in values:values.append(value)
                if values: fields[field]='\n'.join(values)
            claims=claim_text(root)
            if claims:fields['claims']=claims
            return patent.model_copy(update={key:value for key,value in fields.items() if value})
        except (httpx.HTTPError,ET.ParseError):
            return patent
