import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from flask import Flask, jsonify, render_template, request

from regions import VEILIGHEIDSREGIO_KEYWORDS

app = Flask(__name__)

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_rss(root) -> tuple[str, list[dict]]:
    channel = root.find("channel")
    if channel is None:
        channel = root
    title = _text(channel.find("title"))
    items = []
    for item in channel.findall("item"):
        content_el = item.find("content:encoded", NS)
        items.append({
            "title": _text(item.find("title")),
            "link": _text(item.find("link")),
            "summary": _text(item.find("description")),
            "content": _text(content_el),
            "date": _text(item.find("pubDate")) or _text(item.find("dc:date", NS)),
            "tags_text": " ".join(
                _text(c) for c in item.findall("category")
            ),
        })
    return title, items


def _parse_atom(root) -> tuple[str, list[dict]]:
    title_el = root.find("atom:title", NS) or root.find("title")
    feed_title = _text(title_el)
    items = []
    for entry in root.findall("atom:entry", NS) or root.findall("entry"):
        link_el = entry.find("atom:link[@rel='alternate']", NS) or entry.find("atom:link", NS) or entry.find("link")
        link = link_el.get("href", "") if link_el is not None else ""
        summary_el = entry.find("atom:summary", NS) or entry.find("summary")
        content_el = entry.find("atom:content", NS) or entry.find("content")
        title_el = entry.find("atom:title", NS) or entry.find("title")
        date_el = entry.find("atom:updated", NS) or entry.find("updated") or entry.find("atom:published", NS) or entry.find("published")
        items.append({
            "title": _text(title_el),
            "link": link,
            "summary": _text(summary_el),
            "content": _text(content_el),
            "date": _text(date_el),
            "tags_text": " ".join(
                (c.get("term", "") or _text(c))
                for c in (entry.findall("atom:category", NS) or entry.findall("category"))
            ),
        })
    return feed_title, items


def _normalize_date(raw: str) -> str:
    if not raw:
        return ""
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        pass
    return raw


def _item_matches_region(item: dict, keywords: list[str]) -> bool:
    haystack = " ".join([
        item.get("title", ""),
        item.get("summary", ""),
        item.get("content", ""),
        item.get("tags_text", ""),
    ]).lower()
    for kw in keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", haystack):
            return True
    return False


def _fetch_feed(url: str, region_keywords: list[str]) -> dict:
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "RSSRegioReader/1.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except requests.RequestException as exc:
        return {"url": url, "error": str(exc), "items": []}
    except ET.ParseError as exc:
        return {"url": url, "error": f"XML-fout: {exc}", "items": []}

    tag = root.tag.lower()
    try:
        if "feed" in tag:
            feed_title, raw_items = _parse_atom(root)
        else:
            feed_title, raw_items = _parse_rss(root)
    except Exception as exc:
        return {"url": url, "error": f"Parseer-fout: {exc}", "items": []}

    matched = []
    for item in raw_items:
        if _item_matches_region(item, region_keywords):
            matched.append({
                "title": item["title"] or "(geen titel)",
                "link": item["link"],
                "summary": item["summary"] or item["content"][:300],
                "date": _normalize_date(item["date"]),
            })

    return {"url": url, "feed_title": feed_title or url, "items": matched}


@app.route("/")
def index():
    regions = sorted(VEILIGHEIDSREGIO_KEYWORDS.keys())
    return render_template("index.html", regions=regions)


@app.route("/api/filter", methods=["POST"])
def filter_feeds():
    data = request.get_json(force=True)
    urls = [u.strip() for u in data.get("urls", []) if u.strip()]
    region = data.get("region", "")

    if not urls:
        return jsonify({"error": "Geen RSS-feeds opgegeven."}), 400
    if region not in VEILIGHEIDSREGIO_KEYWORDS:
        return jsonify({"error": "Onbekende veiligheidsregio."}), 400

    keywords = VEILIGHEIDSREGIO_KEYWORDS[region]
    results = [_fetch_feed(url, keywords) for url in urls]
    return jsonify({"region": region, "results": results})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
