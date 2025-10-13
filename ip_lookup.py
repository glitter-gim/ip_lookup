# pylint: disable=missing-docstring, unused-argument, unused-import, no-member, disable=global-statement, disable=broad-exception-caught, disable=invalid-name, disable=line-too-long
"""
IP 주소 조회 모듈, /volume1/glitter/glittermy/core/ip_lookup.py
- 여러 외부 API(ipapi, ipinfo, BigDataCloud)를 병렬 호출(히지드)하여 IP 메타데이터 조회
- Redis가 있으면 TTL 캐시 사용(REDIS_URL), 없으면 인메모리 캐시 폴백
- 비동기(httpx) 기반으로 저지연 동작
- 결과는 공통 스키마로 정규화하여 반환
"""
import os, asyncio, time
from typing import Any, Dict, Optional, List, Tuple
import ipaddress
import httpx

# ----------------------------- 설정 -----------------------------
REDIS_URL = os.getenv("REDIS_URL", "")
CACHE_TTL_SEC_DEFAULT = int(os.getenv("IPCACHE_TTL_SEC", "1800"))  # 30m

# ----------------------------- 캐시 -----------------------------
class Cache:
    def __init__(self):
        self._mem: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._redis = None
        if REDIS_URL:
            try:
                import redis.asyncio as aioredis
                # decode_responses=True 로 JSON 문자열 보관
                self._redis = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
            except Exception:
                self._redis = None

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        if self._redis:
            raw = await self._redis.get(key)
            if raw:
                import json
                try:
                    return json.loads(raw)
                except Exception:
                    return None
            return None
        # 인메모리
        now = time.time()
        item = self._mem.get(key)
        if not item:
            return None
        exp, val = item
        if exp < now:
            self._mem.pop(key, None)
            return None
        return val

    async def set(self, key: str, val: Dict[str, Any], ttl: int = CACHE_TTL_SEC_DEFAULT):
        if self._redis:
            import json
            try:
                await self._redis.setex(key, ttl, json.dumps(val))
                return
            except Exception:
                pass
        # 인메모리
        self._mem[key] = (time.time() + ttl, val)

cache = Cache()

# ----------------------------- 유틸 -----------------------------
def _norm_coord(lat: Any, lon: Any) -> Optional[str]:
    try:
        la = float(lat); lo = float(lon)
        if -90 <= la <= 90 and -180 <= lo <= 180:
            return f"{la:.6f},{lo:.6f}"
    except Exception:
        pass
    return None

def _isp_to_company(isp: Optional[str]) -> Dict[str, str]:
    isp = (isp or "").strip()
    company_type = "hosting" if any(k in isp.lower() for k in (
        "aws","amazon","google","gcp","azure","microsoft","cloudflare","ovh","digitalocean","hetzner","aliyun","akamai","fastly","oracle cloud"
    )) else "isp"
    return {"name": isp, "domain": "", "type": company_type}

def _asn_block(asn: Optional[str], org: Optional[str]) -> Dict[str, str]:
    a = (str(asn or "")).upper()
    a = a.lstrip("AS")
    return {"asn": f"AS{a}" if a else "", "name": org or "", "domain": "", "type": "hosting" if "cloud" in (org or "").lower() else "isp"}

def _privacy_guess(company_type: str) -> Dict[str, bool]:
    hosting = (company_type == "hosting")
    return {"vpn": False, "proxy": False, "tor": False, "relay": False, "hosting": hosting}

def _validate_ip(ip: str) -> str:
    ipaddress.ip_address(ip)  # 유효하지 않으면 예외
    return ip

# ----------------------------- 프로바이더 -----------------------------
async def p_ipapi(ip: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """https://ipapi.co/{ip}/json/"""
    try:
        r = await client.get(f"https://ipapi.co/{ip}/json/", timeout=httpx.Timeout(1.2, connect=0.4))
        if r.status_code != 200:
            return None
        d = r.json()
        loc = _norm_coord(d.get("latitude"), d.get("longitude"))
        isp = d.get("org")
        return {
            "ip": d.get("ip") or ip,
            "city": d.get("city"),
            "region": d.get("region"),
            "region_code": d.get("region_code"),
            "country": d.get("country_name"),
            "country_code": d.get("country"),
            "continent": None,
            "continent_code": None,
            "loc": loc,
            "postal": d.get("postal"),
            "timezone": d.get("timezone"),
            "asn": _asn_block(d.get("asn"), d.get("org")),
            "company": _isp_to_company(isp),
            "privacy": _privacy_guess(_isp_to_company(isp)["type"]),
            "source": ["ipapi.co"]
        }
    except Exception:
        return None

async def p_ipinfo(ip: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """https://ipinfo.io/{ip}?token=..."""
    token = os.getenv("IPINFO_TOKEN", "")
    if not token:
        return None
    try:
        r = await client.get(f"https://ipinfo.io/{ip}?token={token}", timeout=httpx.Timeout(1.2, connect=0.4))
        if r.status_code != 200:
            return None
        d = r.json()
        loc = d.get("loc")  # "lat,lon"
        org_field = d.get("org") or ""  # "AS15169 Google LLC"
        isp = org_field.split(" ", 1)[-1] if org_field else None
        asn_code = org_field.split(" ", 1)[0] if org_field else None
        return {
            "ip": d.get("ip") or ip,
            "city": d.get("city"),
            "region": d.get("region"),
            "region_code": None,
            "country": None,
            "country_code": d.get("country"),
            "continent": None,
            "continent_code": None,
            "loc": loc,
            "postal": d.get("postal"),
            "timezone": d.get("timezone"),
            "asn": _asn_block(asn_code, isp),
            "company": _isp_to_company(isp),
            "privacy": _privacy_guess(_isp_to_company(isp)["type"]),
            "source": ["ipinfo.io"]
        }
    except Exception:
        return None

async def p_bigdatacloud(ip: str, client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """
    https://api.bigdatacloud.net/data/ip-geolocation
    BDC 응답 스키마를 풍부하게 매핑(대륙/타임존/행정구역/ASN/조직 등)
    """
    key = os.getenv("BDC_KEY", "")
    if not key:
        return None
    try:
        r = await client.get(
            "https://api.bigdatacloud.net/data/ip-geolocation",
            params={"ip": ip, "localityLanguage": "en", "key": key},
            timeout=httpx.Timeout(1.2, connect=0.4)
        )
        if r.status_code != 200:
            return None

        d = r.json()
        loc = d.get("location") or {}
        country = d.get("country") or {}
        net = d.get("network") or {}

        # 좌표/지역
        coord = _norm_coord(loc.get("latitude"), loc.get("longitude"))
        region_name = loc.get("principalSubdivision") or loc.get("isoPrincipalSubdivision")
        region_code = loc.get("isoPrincipalSubdivisionCode")
        city = loc.get("city") or loc.get("localityName")
        postal = loc.get("postcode") or None
        tz = (loc.get("timeZone") or {}).get("ianaTimeId")
        continent = loc.get("continent")
        continent_code = loc.get("continentCode")

        # 국가 코드/이름
        country_name = country.get("name") or net.get("registeredCountryName")
        # 샘플 응답은 isoAlpha2, 일부 문서엔 isoCode 표기가 있어 둘 다 시도
        country_code = country.get("isoAlpha2") or country.get("isoCode") or net.get("registeredCountry")

        # ASN/조직 - carriers가 우선
        carriers = net.get("carriers") or []
        primary_carrier = carriers[0] if carriers else {}
        asn_code = primary_carrier.get("asn") or net.get("asn")
        org = primary_carrier.get("organisation") or net.get("organisation")

        return {
            "ip": ip,
            "city": city,
            "region": region_name,
            "region_code": region_code,
            "country": country_name,
            "country_code": country_code,
            "continent": continent,
            "continent_code": continent_code,
            "loc": coord,
            "postal": postal,
            "timezone": tz,
            "asn": _asn_block(asn_code, org),
            "company": _isp_to_company(org),
            "privacy": _privacy_guess(_isp_to_company(org)["type"]),
            "source": ["bigdatacloud"]
        }
    except Exception:
        return None

# ----------------------------- 병합 -----------------------------
def _merge_first_good(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not results:
        return None

    # 좌표/타임존/국가/ASN을 기준으로 간단 스코어링
    def score(d: Dict[str, Any]) -> int:
        s = 0
        if d.get("loc"): s += 3
        if d.get("timezone"): s += 1
        if d.get("country") or d.get("country_code"): s += 1
        if d.get("asn", {}).get("asn"): s += 1
        return s

    results.sort(key=score, reverse=True)
    best = results[0]

    # 출처(source) 병합
    srcs: List[str] = []
    for r in results:
        srcs.extend(r.get("source", []))
    best["source"] = list(dict.fromkeys(srcs))

    # 신뢰도(간단 규칙): 다중 소스 + 좌표 있으면 더 높음
    best["confidence"] = 0.9 if len(best["source"]) >= 2 and best.get("loc") else (0.7 if best.get("loc") else 0.6)
    best["age_ms"] = 0
    return best

# ----------------------------- 퍼블릭 API -----------------------------
async def lookup_ip(ip: str) -> Optional[Dict[str, Any]]:
    _validate_ip(ip)
    ck = f"ip:{ip}"
    cached = await cache.get(ck)
    if cached:
        # 응답 노출 시각 기준으로 age_ms는 0으로 통일
        cached["age_ms"] = 0
        return cached

    providers = [p_ipapi, p_ipinfo, p_bigdatacloud]

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"Accept": "application/json"},
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    ) as client:
        # 히지드 요청: 2개 먼저, 200ms 후 나머지
        tasks_primary = [asyncio.create_task(p(ip, client)) for p in providers[:2]]
        await asyncio.sleep(0.2)
        tasks_secondary = [asyncio.create_task(p(ip, client)) for p in providers[2:]]

        done, pending = await asyncio.wait(tasks_primary + tasks_secondary, timeout=1.6)
        results: List[Dict[str, Any]] = []
        for t in done:
            try:
                r = t.result()
                if r:
                    results.append(r)
            except Exception:
                pass
        for p in pending:
            p.cancel()

    merged = _merge_first_good(results)
    if merged:
        ttl = CACHE_TTL_SEC_DEFAULT
        if merged.get("privacy", {}).get("hosting"):
            ttl = min(ttl, 600)  # 호스팅 IP는 TTL 짧게
        await cache.set(ck, merged, ttl=ttl)
    return merged
