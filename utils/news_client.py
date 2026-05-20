import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

try:
    import requests
except ImportError:
    requests = None

_SYMBOL_TO_NAME: dict[str, str] = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035420": "NAVER",
    "035720": "카카오",
    "005380": "현대차",
    "000270": "기아",
    "051910": "LG화학",
    "006400": "삼성SDI",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "068270": "셀트리온",
    "003670": "포스코퓨처엠",
    "440110": "하이브",
    "259960": "크래프톤",
    "012330": "현대모비스",
}

_RSS_FEEDS = [
    {"url": "http://www.yonhapnewstv.co.kr/category/news/economy/feed/", "source": "연합뉴스TV 경제", "economy": True},
    {"url": "http://www.yonhapnewstv.co.kr/browse/feed/", "source": "연합뉴스TV", "economy": False},
    {"url": "https://rss.donga.com/economy.xml", "source": "동아일보 경제", "economy": True},
    {"url": "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml", "source": "조선일보 경제", "economy": True},
    {"url": "http://www.hani.co.kr/rss/economy/", "source": "한겨레 경제", "economy": True},
]


def _parse_pub_date(date_str: str) -> str:
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(date_str).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M")


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _fetch_rss(url: str, source_name: str, timeout: int = 8) -> list[dict[str, Any]]:
    if requests is None:
        return []
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item"):
            title = _strip_html(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            published_at = _parse_pub_date(item.findtext("pubDate") or "")
            description = _strip_html(item.findtext("description") or "")[:200]
            if title and link:
                items.append({
                    "title": title,
                    "link": link,
                    "source_name": source_name,
                    "published_at": published_at,
                    "description": description,
                })
        return items
    except Exception:
        return []


def fetch_company_news(
    *,
    company_name: str,
    symbol: str = "",
    max_items: int = 5,
    days: int = 7,
) -> list[dict[str, Any]]:
    # 샘플 데이터 회사명("005930 종목")은 검색에 무의미하므로 매핑 테이블 우선 사용
    mapped_name = _SYMBOL_TO_NAME.get(symbol.strip(), "")
    real_name = mapped_name or (company_name.strip() if "종목" not in company_name else "")
    keywords = [kw for kw in [real_name, symbol.strip()] if kw]

    seen_links: set[str] = set()
    all_articles: list[dict[str, Any]] = []
    economy_articles: list[dict[str, Any]] = []

    for feed in _RSS_FEEDS:
        for article in _fetch_rss(feed["url"], feed["source"]):
            if article["link"] not in seen_links:
                all_articles.append(article)
                if feed.get("economy"):
                    economy_articles.append(article)
                seen_links.add(article["link"])

    # 키워드 매칭: 없으면 빈 리스트 반환
    if keywords:
        matched = [
            a for a in all_articles
            if any(kw in (a["title"] + a["description"]) for kw in keywords)
        ]
        return _diverse(matched, max_items)

    # 키워드 자체가 없을 때만 경제 뉴스 폴백
    return _diverse(economy_articles, max_items)


def _diverse(articles: list[dict], max_items: int, per_source: int = 0) -> list[dict]:
    """출처별 1건씩 라운드로빈으로 선택해 언론사 편중을 막는다."""
    from collections import defaultdict
    cap = per_source if per_source > 0 else max_items  # 0이면 max_items가 실질적 상한
    buckets: dict[str, list[dict]] = defaultdict(list)
    order: list[str] = []
    for article in articles:
        src = article["source_name"]
        if len(buckets[src]) < cap:
            if src not in order:
                order.append(src)
            buckets[src].append(article)

    result = []
    for i in range(cap):
        for src in order:
            if i < len(buckets[src]):
                result.append(buckets[src][i])
            if len(result) >= max_items:
                return result
    return result
