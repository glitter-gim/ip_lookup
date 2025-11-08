# IP Lookup

Asynchronous multi-provider IP metadata lookup backend.

This module retrieves geolocation, ASN, organization, and privacy information for any IP address.  
It aggregates data from multiple **free public APIs** — [`ipapi.co`](https://ipapi.co/api/), [`ipinfo.io`](https://ipinfo.io/developers), and [`BigDataCloud`](https://www.bigdatacloud.com/ip-geolocation-apis/free-ip-geolocation-api) — and merges them into a unified JSON schema.  
It works **immediately without any account or API key**, while supporting optional tokens for enhanced accuracy.

---

## 🚀 Features
- **Account-free operation** — works out-of-the-box using ipapi.co free tier.
- **Automatic provider expansion** via environment variables:
  - `IPINFO_TOKEN` → enables ipinfo.io integration  
  - `BDC_KEY` → enables BigDataCloud community API  
- **Asynchronous (httpx + asyncio)** low-latency design.
- **Smart merge engine** (`_merge_first_good`) — ranks and normalizes results.
- **Redis / in-memory caching** with TTL for faster repeated lookups.
- Unified, normalized response structure across all sources.

---

## ⚙️ Environment Variables

| Variable | Description | Example |
|-----------|--------------|----------|
| `REDIS_URL` | Optional Redis connection string (used for cache) | `redis://localhost:6379/0` |
| `IPCACHE_TTL_SEC` | Default cache lifetime (seconds) | `1800` |
| `IPINFO_TOKEN` | Optional token for ipinfo.io API | `<your_token>` |
| `BDC_KEY` | Optional API key for BigDataCloud | `<your_key>` |

If no tokens are provided, the module still works using ipapi.co’s public endpoint.

---

## 🧠 Example Usage

### Basic Python Call
```python
import asyncio
from core.ip_lookup import lookup_ip

result = asyncio.run(lookup_ip("8.8.8.8"))
print(result)

---



Copyright © 2025 glitter.kr
Author: glitter💫
Trust Chain: DNSSEC · DANE · HSTS · CSP
GitHub-hosted IP Lookup Backend Source

