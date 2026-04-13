#!/usr/bin/env python3
"""Local Crawl4AI tools.

Wrap a locally running Crawl4AI Docker service as native Hermes tools.
These tools are intended as a trusted local scraping lane when normal web
search/extract backends are insufficient.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:11235"
_TIMEOUT = 60.0


def _base_url() -> str:
    return (os.getenv("CRAWL4AI_BASE_URL", _DEFAULT_BASE_URL) or _DEFAULT_BASE_URL).rstrip("/")


def check_crawl4ai_local_requirements() -> bool:
    """Return True when the local Crawl4AI service responds healthy."""
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{_base_url()}/health")
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "ok"
    except Exception as e:
        logger.debug("crawl4ai local check failed: %s", e)
        return False


def _post_json(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(f"{_base_url()}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def crawl4ai_markdown(
    url: str,
    mode: str = "fit",
    query: Optional[str] = None,
    cache_bust: str = "0",
) -> str:
    """Convert a page to markdown using local Crawl4AI."""
    if not url or not isinstance(url, str):
        return tool_error("url is required")
    if mode not in {"fit", "raw", "bm25", "llm"}:
        return tool_error("mode must be one of: fit, raw, bm25, llm")

    payload: Dict[str, Any] = {"url": url, "f": mode, "c": cache_bust}
    if query:
        payload["q"] = query

    try:
        data = _post_json("/md", payload)
        return json.dumps(
            {
                "success": bool(data.get("success", True)),
                "url": data.get("url", url),
                "filter": data.get("filter", mode),
                "query": data.get("query"),
                "markdown": data.get("markdown", ""),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return tool_error(f"crawl4ai markdown request failed: {e}")


def crawl4ai_scrape(
    url: str,
    cache_mode: str = "bypass",
    word_count_threshold: int = 1,
    screenshot: bool = False,
    pdf: bool = False,
) -> str:
    """Run a raw Crawl4AI scrape against one URL."""
    if not url or not isinstance(url, str):
        return tool_error("url is required")

    payload: Dict[str, Any] = {
        "urls": [url],
        "priority": 10,
        "browser_config": {
            "headless": True,
            "verbose": False,
        },
        "crawler_config": {
            "cache_mode": cache_mode,
            "word_count_threshold": int(word_count_threshold),
            "screenshot": bool(screenshot),
            "pdf": bool(pdf),
        },
    }

    try:
        data = _post_json("/crawl", payload)
        results = data.get("results") or []
        result = results[0] if results else {}
        return json.dumps(
            {
                "success": bool(data.get("success", True)),
                "url": result.get("url", url),
                "html": result.get("html", ""),
                "markdown": result.get("markdown"),
                "cleaned_html": result.get("cleaned_html"),
                "media": result.get("media"),
                "links": result.get("links"),
                "metadata": result.get("metadata"),
                "screenshot": result.get("screenshot"),
                "pdf": result.get("pdf"),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return tool_error(f"crawl4ai scrape request failed: {e}")


CRAWL4AI_MARKDOWN_SCHEMA = {
    "name": "crawl4ai_markdown",
    "description": "Use the local Crawl4AI service to convert a web page into markdown. Best for trusted local scraping when normal web extract/search tools are insufficient.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http/https URL to fetch."},
            "mode": {
                "type": "string",
                "enum": ["fit", "raw", "bm25", "llm"],
                "description": "Markdown extraction mode. fit is the default clean readable mode.",
                "default": "fit"
            },
            "query": {"type": "string", "description": "Optional query for bm25 or llm modes."},
            "cache_bust": {"type": "string", "description": "Optional cache-bust revision token.", "default": "0"}
        },
        "required": ["url"]
    }
}

CRAWL4AI_SCRAPE_SCHEMA = {
    "name": "crawl4ai_scrape",
    "description": "Use the local Crawl4AI service to scrape a page and return raw HTML plus structured scrape artifacts. Use this for hard pages or when you need lower-level extraction.",
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http/https URL to scrape."},
            "cache_mode": {
                "type": "string",
                "enum": ["enabled", "disabled", "read_only", "write_only", "bypass"],
                "description": "Crawl4AI cache mode.",
                "default": "bypass"
            },
            "word_count_threshold": {"type": "integer", "description": "Minimum word threshold passed to Crawl4AI.", "default": 1},
            "screenshot": {"type": "boolean", "description": "Whether to request a screenshot artifact.", "default": False},
            "pdf": {"type": "boolean", "description": "Whether to request a PDF artifact.", "default": False}
        },
        "required": ["url"]
    }
}


def _handle_crawl4ai_markdown(args: Dict[str, Any], **kw) -> str:
    return crawl4ai_markdown(
        url=args.get("url", ""),
        mode=args.get("mode", "fit"),
        query=args.get("query"),
        cache_bust=args.get("cache_bust", "0"),
    )


def _handle_crawl4ai_scrape(args: Dict[str, Any], **kw) -> str:
    return crawl4ai_scrape(
        url=args.get("url", ""),
        cache_mode=args.get("cache_mode", "bypass"),
        word_count_threshold=args.get("word_count_threshold", 1),
        screenshot=bool(args.get("screenshot", False)),
        pdf=bool(args.get("pdf", False)),
    )


registry.register(
    name="crawl4ai_markdown",
    toolset="web",
    schema=CRAWL4AI_MARKDOWN_SCHEMA,
    handler=_handle_crawl4ai_markdown,
    check_fn=check_crawl4ai_local_requirements,
    requires_env=[],
    is_async=False,
    emoji="🕷️",
)

registry.register(
    name="crawl4ai_scrape",
    toolset="web",
    schema=CRAWL4AI_SCRAPE_SCHEMA,
    handler=_handle_crawl4ai_scrape,
    check_fn=check_crawl4ai_local_requirements,
    requires_env=[],
    is_async=False,
    emoji="🕷️",
)
