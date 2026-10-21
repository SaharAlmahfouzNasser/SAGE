"""
Seam ①: Paperclip corpus loader.

Replaces PyPDFDirectoryLoader + hand-curated PDFs with a query-driven,
reproducible corpus. Returns papers in the shape that build_kg.build_sage_kg()
and sage_kg.filter_papers() expect.

A corpus build is fully described by (query, source, n, since, date_end) plus
the Paperclip result_id returned in each paper's _paperclip_result_id field,
so any run can be exactly reproduced or audited.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
for _pkg in (_ROOT / "HypothesisGeneration-pkg",):
    if _pkg.exists() and str(_pkg) not in sys.path:
        sys.path.insert(0, str(_pkg))

from HypothesisGeneration.paperclip_tools import lit_grep, lit_search

# Reuse the grep pattern from the extractor for consistency
_RELATION_CUES = (
    r"associated with|inhibit|activat|upregulat|downregulat|regulat|"
    r"express|biomarker|predict|diagnos|treat|target|resistance|response|"
    r"mutat|variant|pathway|interact|induc|suppress|promot|causes|trigger|"
    r"correlat|stratif|prognos|marker for|located in|part of|measured by"
)


def fetch_corpus(
    query: str,
    source: str = "pmc",
    n: int = 20,
    since: Optional[str] = None,
    date_end: Optional[str] = None,
    augment_with_grep: bool = False,
) -> List[Dict]:
    """Fetch papers from Paperclip and return them in build_kg paper format.

    Each returned dict contains:
        pmid, doi, title, abstract, full_text, year, journal, url,
        citationCount, _paperclip_result_id

    full_text is the abstract by default.  Set augment_with_grep=True to append
    relation-bearing excerpt paragraphs fetched via lit_grep — longer and richer,
    but adds a second API call.

    Args:
        query             : Literature search query.
        source            : Paperclip corpus — "pmc", "biorxiv", "medrxiv", etc.
        n                 : Papers to retrieve.
        since             : Start date filter "YYYY-MM".
        date_end          : End date filter "YYYY-MM" (time-travel hygiene: set to
                            a past date to exclude more recent papers from the build).
        augment_with_grep : Append grep'd relation excerpts to full_text.

    Returns:
        List of paper dicts ready for build_kg.build_sage_kg(papers).

    Example::

        papers = fetch_corpus(
            "prostate cancer androgen receptor resistance",
            source="pmc", n=30, date_end="2024-01",
        )
        graphml, G = build_sage_kg(papers)
    """
    result_id, raw_papers = lit_search(
        query, source=source, n=n, since=since, date_end=date_end
    )

    papers: List[Dict] = []
    for p in raw_papers:
        pmid = str(p.get("pmid") or p.get("id") or "")
        year = p.get("year") or ""
        if not year and p.get("date"):
            year = str(p["date"])[:4]

        paper: Dict = {
            "pmid": pmid,
            "doi": p.get("doi") or "",
            "title": p.get("title") or "",
            "abstract": p.get("abstract") or p.get("snippet") or "",
            "full_text": p.get("abstract") or p.get("snippet") or "",
            "year": year,
            "journal": p.get("journal") or p.get("venue") or "",
            "url": p.get("url") or "",
            "citationCount": int(p.get("citationCount") or p.get("citation_count") or 0),
            "_paperclip_result_id": result_id,
        }
        papers.append(paper)

    if augment_with_grep and result_id:
        excerpts: Dict[str, List[str]] = {}
        for para in lit_grep(result_id, _RELATION_CUES):
            pid = str(para.get("pmid") or para.get("paper_id") or "")
            txt = (para.get("text") or "").strip()
            if pid and txt:
                excerpts.setdefault(pid, []).append(txt)

        for paper in papers:
            pid = paper["pmid"]
            if pid in excerpts:
                paper["full_text"] = (
                    paper["abstract"] + "\n\n" + "\n\n".join(excerpts[pid])
                ).strip()

    return papers
