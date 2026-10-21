"""
Build a biomedical knowledge graph (SAGE pipeline) from a topic's literature.

End-to-end:
  Phase 2 (search) : PubMed (NCBI E-utils) + Semantic Scholar (keyless).
  Phase 3 (full text): for each paper try, in order,
                       PMC efetch -> Europe PMC full-text XML -> Unpaywall OA PDF
                       (3-tier parse) -> abstract fallback.
  Stage A-D (sage_kg.py): SapBERT relevance filter -> typed-triple extraction
                       (GPT-4o-mini, c>=0.5) -> per-document local graphs ->
                       BGE-Large fusion (tau=0.8) -> prune -> GraphML.

Input  : keywords/expanded_search_queries.json  (from expand_keywords.py)
Output : outputs/sage_kg/prostate_cancer_kg.graphml  (+ typed node/edge JSON)

Run Phase 1 first:  python expand_keywords.py
Then this:          python build_kg.py
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from xml.etree import ElementTree as ET

import requests

# =============================================================================
# Config / logging / keys
# =============================================================================

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "build_kg.log", encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("build_kg")

if Path(".env").exists():
    for line in open(".env"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
_ncbi = os.environ.get("NCBI_API_KEY", "")
_s2 = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
NCBI_API_KEY = _ncbi if _ncbi and not _ncbi.startswith("your-") and len(_ncbi) > 5 else ""
S2_API_KEY = _s2 if _s2 and not _s2.startswith("your-") and len(_s2) > 5 else ""
UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "jduke99@gmail.com")

CORPUS_DIR = Path("inputs/sage_kg")
OUTPUT_DIR = Path("outputs/sage_kg")
EMBED_CACHE_DIR = CORPUS_DIR / "embed_cache"
for d in (CORPUS_DIR, OUTPUT_DIR, EMBED_CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX = "https://api.openalex.org/works"
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "jduke99@gmail.com")
GRAPH_ROOT = "prostate_cancer_kg"  # fallback only — normal builds now derive this from --domain


def domain_to_graph_root(domain: str) -> str:
    """Turn a free-text research domain into a filesystem-safe graph_root,
    e.g. 'Alzheimer's disease' -> 'alzheimers_disease_kg'. Falls back to the
    module default if domain is empty/unusable."""
    slug = re.sub(r"[^a-z0-9]+", "_", (domain or "").lower()).strip("_")
    return f"{slug}_kg" if slug else GRAPH_ROOT

log.info("Keys: OPENAI=%s NCBI=%s S2=%s",
         "SET" if OPENAI_API_KEY else "MISSING",
         "SET" if NCBI_API_KEY else "none", "SET" if S2_API_KEY else "keyless")

errors: Dict[str, List] = {"pubmed": [], "s2": [], "clinicaltrials": [], "fulltext": [], "kg_build": []}


def track_error(source: str, ctx: str, msg) -> None:
    errors[source].append({"ctx": ctx[:120], "error": str(msg), "time": datetime.now().isoformat()})
    log.error("[%s] %s", source.upper(), msg)


# =============================================================================
# Phase 2a: PubMed search (NCBI E-utils)
# =============================================================================

def pubmed_search(query: str, max_results: int = 25, api_key: str = "") -> List[Dict]:
    """Search PubMed, return records with pmid/pmcid/doi/title/abstract/year."""
    papers = []
    try:
        params = {"db": "pubmed", "term": query, "retmax": max_results,
                  "retmode": "json", "sort": "relevance"}
        if api_key:
            params["api_key"] = api_key
        r = requests.get(f"{NCBI}/esearch.fcgi", params=params, timeout=15)
        r.raise_for_status()
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return papers

        time.sleep(0.34)
        fp = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if api_key:
            fp["api_key"] = api_key
        fr = requests.get(f"{NCBI}/efetch.fcgi", params=fp, timeout=30)
        fr.raise_for_status()
        root = ET.fromstring(fr.content)

        for art in root.findall(".//PubmedArticle"):
            te = art.find(".//ArticleTitle")
            title = te.text if te is not None and te.text else ""
            abstract = " ".join(a.text for a in art.findall(".//AbstractText") if a.text)
            pe = art.find(".//PMID")
            pmid = pe.text if pe is not None else ""
            ye = art.find(".//PubDate/Year")
            year = ye.text if ye is not None else ""
            # Journal title (for venue / impact-factor scoring in Stage A).
            je = art.find(".//Journal/Title")
            jae = art.find(".//Journal/ISOAbbreviation")
            journal = (je.text if je is not None and je.text else
                       (jae.text if jae is not None and jae.text else ""))
            pmcid, doi = "", ""
            for aid in art.findall(".//ArticleId"):
                if aid.get("IdType") == "pmc":
                    pmcid = aid.text or ""
                elif aid.get("IdType") == "doi":
                    doi = aid.text or ""
            if title or abstract:
                papers.append({
                    "title": title, "abstract": abstract, "year": year,
                    "pmid": pmid, "pmcid": pmcid, "doi": doi, "journal": journal,
                    "citationCount": 0, "source": "pubmed", "query": query,
                })
    except requests.exceptions.HTTPError as e:
        track_error("pubmed", query, f"HTTP {e.response.status_code}")
    except Exception as e:
        track_error("pubmed", query, str(e))
    return papers


# =============================================================================
# Phase 2b: Semantic Scholar search (keyless or keyed)
# =============================================================================

_s2_state = {"consecutive_403s": 0}


def s2_search(query: str, max_results: int = 25, api_key: str = "") -> List[Dict]:
    """Search Semantic Scholar. Keyless works but is throttled/shared; on repeated
    403/429 we stop calling S2 for the rest of the run."""
    papers = []
    if _s2_state["consecutive_403s"] >= 5:
        return papers
    try:
        headers = {"x-api-key": api_key} if api_key else None
        params = {"query": query, "limit": min(max_results, 100),
                  "fields": "title,abstract,year,venue,externalIds,url,citationCount,influentialCitationCount"}
        time.sleep(0.2 if api_key else 1.1)
        r = requests.get(S2_SEARCH, params=params, headers=headers, timeout=15)

        retries = 0
        while r.status_code == 429 and retries < 3:
            wait = 5 * (retries + 1)
            log.warning("  [S2] 429, retry %d/3 after %ds", retries + 1, wait)
            time.sleep(wait)
            r = requests.get(S2_SEARCH, params=params, headers=headers, timeout=15)
            retries += 1

        if r.status_code == 403:
            _s2_state["consecutive_403s"] += 1
            return papers
        r.raise_for_status()
        _s2_state["consecutive_403s"] = 0

        for it in r.json().get("data", []):
            title = it.get("title", "")
            abstract = it.get("abstract") or ""
            doi = (it.get("externalIds") or {}).get("DOI", "")
            pmid = (it.get("externalIds") or {}).get("PubMed", "")
            pmcid = (it.get("externalIds") or {}).get("PMCID", "")
            if title or abstract:
                papers.append({
                    "title": title, "abstract": abstract, "year": str(it.get("year", "")),
                    "pmid": pmid, "pmcid": pmcid, "doi": doi,
                    "journal": it.get("venue", ""),
                    "citationCount": int(it.get("citationCount", 0) or 0),
                    "influentialCitationCount": int(it.get("influentialCitationCount", 0) or 0),
                    "source": "semantic_scholar", "query": query,
                })
    except requests.exceptions.HTTPError as e:
        track_error("s2", query, f"HTTP {e.response.status_code}")
    except Exception as e:
        track_error("s2", query, str(e))
    return papers


# =============================================================================
# Phase 2c: ClinicalTrials.gov search (API v2, keyless)
# =============================================================================

CTGOV_API = "https://clinicaltrials.gov/api/v2/studies"


NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)


def extract_nct_id(text: str) -> str:
    """Pull an NCT ID out of a clinicaltrials.gov URL or bare string, e.g.
    'https://clinicaltrials.gov/study/NCT01234567' -> 'NCT01234567'. '' if none."""
    m = NCT_ID_RE.search(text or "")
    return m.group(0).upper() if m else ""


def _parse_ct_study(s: Dict, query: str = "") -> Dict | None:
    """Shared parser: one ClinicalTrials.gov API v2 study object -> a paper-shaped
    dict (see module docstring for why this shape). Returns None if there isn't
    enough content to be useful (extract_triples needs real text to work with)."""
    proto = s.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    desc = proto.get("descriptionModule", {})
    cond = proto.get("conditionsModule", {})
    arms = proto.get("armsInterventionsModule", {})
    outcomes = proto.get("outcomesModule", {})
    status = proto.get("statusModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {})

    nct_id = ident.get("nctId", "")
    title = ident.get("briefTitle", "") or ident.get("officialTitle", "")
    summary = desc.get("briefSummary", "") or ""
    detailed = desc.get("detailedDescription", "") or ""
    conditions = ", ".join(cond.get("conditions", []) or [])
    interventions = "; ".join(
        f"{iv.get('type', '')}: {iv.get('name', '')}"
        for iv in (arms.get("interventions", []) or [])
    )
    primary_outcomes = "; ".join(
        o.get("measure", "") for o in (outcomes.get("primaryOutcomes", []) or [])
    )
    start_date = (status.get("startDateStruct", {}) or {}).get("date", "")
    year = start_date[:4] if start_date else ""
    lead_sponsor = (sponsor.get("leadSponsor", {}) or {}).get("name", "")

    full_text_parts = [
        f"Title: {title}",
        f"Conditions: {conditions}" if conditions else "",
        f"Summary: {summary}" if summary else "",
        f"Detailed description: {detailed}" if detailed else "",
        f"Interventions: {interventions}" if interventions else "",
        f"Primary outcomes: {primary_outcomes}" if primary_outcomes else "",
    ]
    full_text = "\n".join(part for part in full_text_parts if part)

    if not (title and (summary or detailed)):
        return None  # not enough content to extract triples from

    return {
        "title": title,
        "abstract": summary or full_text[:600],
        "full_text": full_text,
        "text_source": "clinicaltrials",
        "year": year,
        "pmid": "",
        "pmcid": "",
        "doi": "",
        "nctid": nct_id,
        "journal": lead_sponsor,  # reused as a loose "venue" signal for Stage A
        "citationCount": 0,
        "source": "clinicaltrials",
        "query": query,
        "query_type": "clinical_trial",
    }


def clinicaltrials_search(query: str, max_results: int = 25) -> List[Dict]:
    """Search ClinicalTrials.gov (API v2) and return records shaped like the
    paper dicts used elsewhere in the pipeline.

    Full text is pre-populated from the trial's summary / detailed description /
    interventions / primary outcomes, so these records bypass Phase 3 (full-text
    retrieval) the same way manual_docs entries do — they go straight into
    Stage A filtering with text already attached.
    """
    papers = []
    try:
        params = {
            "query.term": query,
            "pageSize": min(max_results, 100),
            "format": "json",
        }
        r = requests.get(CTGOV_API, params=params, timeout=20)
        r.raise_for_status()
        for s in r.json().get("studies", []):
            p = _parse_ct_study(s, query=query)
            if p:
                papers.append(p)
    except requests.exceptions.HTTPError as e:
        track_error("clinicaltrials", query, f"HTTP {e.response.status_code}")
    except Exception as e:
        track_error("clinicaltrials", query, str(e))
    return papers


def clinicaltrials_fetch_by_id(nct_id: str) -> Dict | None:
    """Fetch a single trial directly by NCT ID (GET /studies/{nctId}) — used
    when a person pastes a specific clinicaltrials.gov URL into 'Add Source'
    rather than running a broad search."""
    try:
        r = requests.get(f"{CTGOV_API}/{nct_id}", params={"format": "json"}, timeout=20)
        r.raise_for_status()
        return _parse_ct_study(r.json(), query=f"direct:{nct_id}")
    except requests.exceptions.HTTPError as e:
        track_error("clinicaltrials", nct_id, f"HTTP {e.response.status_code}")
        return None
    except Exception as e:
        track_error("clinicaltrials", nct_id, str(e))
        return None


def fetch_all_clinicaltrials(queries_file: str, max_per_query: int) -> List[Dict]:
    """Run a list of queries against ClinicalTrials.gov and return combined
    trial records. queries_file uses the same schema as the main PubMed
    queries file: a JSON list of {category, subcategory, query, rationale}.

    This is additive — it does not replace or narrow the default PubMed +
    Semantic Scholar search in fetch_all_papers(); results are merged in
    before Stage A filtering (see main()).
    """
    p = Path(queries_file)
    if not p.exists():
        log.warning("ClinicalTrials queries file not found: %s (skipping)", queries_file)
        return []
    with open(p) as f:
        queries = json.load(f)

    log.info("=" * 60)
    log.info("PHASE 2c: Search ClinicalTrials.gov (%d queries, <=%d/query)",
             len(queries), max_per_query)
    log.info("=" * 60)

    all_trials = []
    for i, q in enumerate(queries, 1):
        query = q["query"]
        label = f"{q.get('category', '')}/{q.get('subcategory', '')}".strip("/")
        trials = clinicaltrials_search(query, max_per_query)
        all_trials.extend(trials)
        log.info("[ct %d/%d] %-38s trials=%d", i, len(queries), label[:38], len(trials))

    log.info("ClinicalTrials.gov: %d raw trial records", len(all_trials))
    return all_trials


# =============================================================================
# Manual documents (textbooks, preprints, etc.)
# =============================================================================

def load_manual_docs(path: str) -> List[Dict]:
    """Load manually curated documents from a JSON file.

    Each entry must have at least "title" and "full_text". Optional fields:
    "abstract", "year", "source", "journal". Documents are injected directly
    into the pipeline after search/dedup — they bypass Phase 3 (full-text
    retrieval) and go straight through Stage A–D.

    Example entry:
        {"title": "Prostate Cancer", "source": "statpearls",
         "year": "2024", "full_text": "Prostate cancer is ..."}
    """
    p = Path(path)
    if not p.exists():
        return []
    docs = json.load(open(p))
    out = []
    for d in docs:
        if not d.get("title") or not d.get("full_text"):
            log.warning("  [manual_docs] skipping entry missing title or full_text")
            continue
        out.append({
            "title": d["title"],
            "abstract": d.get("abstract", d["full_text"][:600]),
            "full_text": d["full_text"],
            "text_source": d.get("source", "manual"),
            "year": str(d.get("year", "")),
            "pmid": d.get("pmid", ""),
            "pmcid": d.get("pmcid", ""),
            "doi": d.get("doi", ""),
            "journal": d.get("journal", ""),
            "citationCount": 0,
            "source": d.get("source", "manual"),
            "query": "manual",
            "query_type": "textbook",
        })
    log.info("Manual docs loaded: %d documents from %s", len(out), path)
    return out


# =============================================================================
# Dedup + fetch orchestration
# =============================================================================

def deduplicate(papers: List[Dict]) -> List[Dict]:
    """Dedup by DOI, then PMID, then NCT ID, then normalized title. Merge citation counts."""
    by_key: Dict[str, Dict] = {}
    order: List[str] = []
    dupes = 0
    for p in papers:
        key = ((p.get("doi") or "").lower() or (p.get("pmid") or "")
               or (p.get("nctid") or "") or p.get("title", "").lower().strip())
        if not key:
            continue
        if key not in by_key:
            by_key[key] = p
            order.append(key)
        else:
            dupes += 1
            ex = by_key[key]
            # keep richer record: prefer one with more identifiers / citations
            ex["citationCount"] = max(ex.get("citationCount", 0), p.get("citationCount", 0))
            for f in ("pmcid", "doi", "pmid", "nctid", "abstract", "journal"):
                if not ex.get(f) and p.get(f):
                    ex[f] = p[f]
    log.info("Dedup: %d removed, %d unique", dupes, len(order))
    return [by_key[k] for k in order]


def enrich_openalex(papers: List[Dict]) -> List[Dict]:
    """Fill citationCount + journal from OpenAlex (free, no key) for papers with a DOI.

    OpenAlex replaces Semantic Scholar for the citation/venue signal used by the
    Stage A filter. Batched by DOI via the works filter endpoint (50/req).
    """
    log.info("=" * 60)
    log.info("OpenAlex enrichment (citations + venue)")
    log.info("=" * 60)
    with_doi = [p for p in papers if p.get("doi")]
    if not with_doi:
        log.info("  no DOIs to enrich")
        return papers
    by_doi = {p["doi"].lower(): p for p in with_doi}

    enriched = 0
    dois = list(by_doi.keys())
    for s in range(0, len(dois), 50):
        batch = dois[s:s + 50]
        filt = "doi:" + "|".join(batch)  # OpenAlex OR-filter on DOIs
        try:
            time.sleep(0.15)
            r = requests.get(OPENALEX, timeout=30, params={
                "filter": filt, "per_page": 50, "mailto": OPENALEX_MAILTO,
                "select": "doi,cited_by_count,primary_location",
            })
            if r.status_code != 200:
                continue
            for w in r.json().get("results", []):
                wdoi = (w.get("doi") or "").lower().replace("https://doi.org/", "")
                p = by_doi.get(wdoi)
                if not p:
                    continue
                p["citationCount"] = int(w.get("cited_by_count", 0) or 0)
                src = (w.get("primary_location") or {}).get("source") or {}
                if src.get("display_name") and not p.get("journal"):
                    p["journal"] = src["display_name"]
                enriched += 1
        except Exception as e:
            log.debug("  [OpenAlex] batch failed: %s", e)
        if (s // 50) % 5 == 0:
            log.info("  enriched %d/%d", min(s + 50, len(dois)), len(dois))

    cited = sum(1 for p in papers if p.get("citationCount", 0) > 0)
    log.info("  enriched %d papers; %d now have citationCount>0", enriched, cited)
    return papers


def fetch_all_papers(queries_file: str, max_per_query: int, use_s2: bool,
                     comp_queries_file: str = "", comp_frac: float = 0.0) -> List[Dict]:
    with open(queries_file) as f:
        queries = json.load(f)
    comp_queries = []
    if comp_frac > 0 and comp_queries_file and Path(comp_queries_file).exists():
        with open(comp_queries_file) as f:
            comp_queries = json.load(f)
    elif comp_frac > 0 and comp_queries_file:
        log.warning("comp-queries file not found: %s (skipping computational papers)", comp_queries_file)
    log.info("=" * 60)
    log.info("PHASE 2: Search PubMed%s (%d bio + %d comp queries, <=%d/query)",
             " + Semantic Scholar" if use_s2 else "",
             len(queries), len(comp_queries), max_per_query)
    log.info("=" * 60)

    all_papers, start = [], time.time()
    for i, q in enumerate(queries, 1):
        query = q["query"]
        label = f"{q.get('category','')}/{q.get('subcategory','')}".strip("/")
        pm = pubmed_search(query, max_per_query, api_key=NCBI_API_KEY)
        s2 = s2_search(query, max_per_query, api_key=S2_API_KEY) if use_s2 else []
        for p in pm + s2:
            p["query_type"] = "biomedical"
        all_papers.extend(pm)
        all_papers.extend(s2)
        log.info("[%d/%d] %-38s PubMed=%d S2=%d", i, len(queries), label[:38], len(pm), len(s2))

    comp_raw = []
    for i, q in enumerate(comp_queries, 1):
        query = q["query"]
        label = f"comp/{q.get('subcategory', '')}"[:38]
        pm = pubmed_search(query, max_per_query, api_key=NCBI_API_KEY)
        s2 = s2_search(query, max_per_query, api_key=S2_API_KEY) if use_s2 else []
        for p in pm + s2:
            p["query_type"] = "computational"
        comp_raw.extend(pm)
        comp_raw.extend(s2)
        log.info("[comp %d/%d] %-38s PubMed=%d S2=%d", i, len(comp_queries), label, len(pm), len(s2))
    all_papers.extend(comp_raw)

    log.info("Search done in %.1f min, %d raw papers", (time.time() - start) / 60, len(all_papers))
    deduped = deduplicate(all_papers)

    if comp_frac > 0 and comp_queries:
        bio = [p for p in deduped if p.get("query_type") != "computational"]
        comp = [p for p in deduped if p.get("query_type") == "computational"]
        max_comp = max(1, round(len(bio) * comp_frac / max(1 - comp_frac, 0.01)))
        if len(comp) > max_comp:
            comp = comp[:max_comp]
        log.info("Comp fraction %.0f%%: %d comp papers, %d bio papers (total %d)",
                 comp_frac * 100, len(comp), len(bio), len(bio) + len(comp))
        deduped = bio + comp

    return deduped


# =============================================================================
# Phase 3: Full-text retrieval
#   PMC efetch -> Europe PMC full-text XML -> Unpaywall OA PDF -> abstract
# =============================================================================

def _xml_text(elem) -> str:
    return " ".join(t.strip() for t in elem.itertext() if t and t.strip())


def fetch_pmc_efetch(pmcid: str, api_key: str = "") -> str:
    """NCBI PMC full text via efetch db=pmc. '' on failure."""
    if not pmcid:
        return ""
    try:
        time.sleep(0.34)
        params = {"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"}
        if api_key:
            params["api_key"] = api_key
        r = requests.get(f"{NCBI}/efetch.fcgi", params=params, timeout=30)
        r.raise_for_status()
        body = ET.fromstring(r.content).find(".//body")
        if body is None:
            return ""
        text = _xml_text(body)
        return text if len(text) > 500 else ""
    except Exception as e:
        log.debug("  [PMC efetch] %s: %s", pmcid, e)
        return ""


def fetch_epmc_xml(pmcid: str) -> str:
    """Europe PMC full-text XML. '' on failure."""
    if not pmcid:
        return ""
    try:
        time.sleep(0.2)
        r = requests.get(f"{EPMC}/{pmcid}/fullTextXML", timeout=30)
        if r.status_code != 200:
            return ""
        body = ET.fromstring(r.content).find(".//body")
        if body is None:
            return ""
        text = _xml_text(body)
        return text if len(text) > 500 else ""
    except Exception as e:
        log.debug("  [EPMC XML] %s: %s", pmcid, e)
        return ""


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """3-tier PDF text extraction: pdfplumber -> PyMuPDF -> pypdf."""
    import io
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            t = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if len(t.strip()) > 500:
            return t
    except Exception:
        pass
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        t = "\n".join(pg.get_text() for pg in doc)
        if len(t.strip()) > 500:
            return t
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        t = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf_bytes)).pages)
        if len(t.strip()) > 500:
            return t
    except Exception:
        pass
    return ""


def fetch_unpaywall_pdf_text(doi: str) -> str:
    """Resolve OA PDF via Unpaywall, download, extract text. '' on failure."""
    if not doi:
        return ""
    try:
        time.sleep(0.2)
        r = requests.get(f"https://api.unpaywall.org/v2/{doi}",
                         params={"email": UNPAYWALL_EMAIL}, timeout=15)
        if r.status_code != 200:
            return ""
        loc = (r.json() or {}).get("best_oa_location") or {}
        pdf_url = loc.get("url_for_pdf")
        if not pdf_url:
            return ""
        pr = requests.get(pdf_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if pr.status_code != 200 or not pr.content[:5].startswith(b"%PDF"):
            return ""
        return _extract_pdf_text(pr.content)
    except Exception as e:
        log.debug("  [Unpaywall] %s: %s", doi, e)
        return ""


def attach_fulltext(papers: List[Dict], ncbi_key: str = "") -> List[Dict]:
    """Populate full_text + text_source per paper via the fallback chain."""
    log.info("=" * 60)
    log.info("PHASE 3: Full-text retrieval (PMC -> EuropePMC -> Unpaywall -> abstract)")
    log.info("=" * 60)
    counts = {"pmc_efetch": 0, "epmc_xml": 0, "unpaywall_pdf": 0, "abstract": 0, "pre_fetched": 0}
    for i, p in enumerate(papers, 1):
        if p.get("full_text") and p.get("text_source") not in ("abstract", "", None):
            counts["pre_fetched"] += 1
            continue
        text, source = "", "abstract"
        pmcid = p.get("pmcid", "")
        if pmcid:
            text = fetch_pmc_efetch(pmcid, api_key=ncbi_key)
            if text:
                source = "pmc_efetch"
            else:
                text = fetch_epmc_xml(pmcid)
                if text:
                    source = "epmc_xml"
        if not text and p.get("doi"):
            text = fetch_unpaywall_pdf_text(p["doi"])
            if text:
                source = "unpaywall_pdf"
        if not text:
            text = f"{p.get('title','')}\n{p.get('abstract','')}".strip()
            source = "abstract"
        p["full_text"], p["text_source"] = text, source
        counts[source] += 1
        if i % 50 == 0:
            log.info("  [%d/%d] %s", i, len(papers), counts)
    log.info("Full-text sources: %s", counts)
    return papers


def filter_papers_weighted(papers: List[Dict], queries: List[Dict], cache_dir: Path,
                           drop_frac: float, source_weights: Dict[str, float] | None = None) -> List[Dict]:
    """Run Stage A relevance filtering (sage_kg.filter_papers) per-source group
    instead of globally, so specific sources can be prioritized relative to
    others when building a graph.

    source_weights maps a paper's "source" field (e.g. "clinicaltrials",
    "pubmed", "semantic_scholar") to a relative weight. Weight 1.0 = default
    drop_frac for that source. Weight 2.0 halves that source's drop_frac
    (keeps roughly 2x as much of it, relative to the baseline); weight 0.5
    doubles it (drops more). Sources not listed default to weight 1.0.

    This does not change the underlying relevance scoring (still SapBERT +
    venue + citations, same as sage_kg.filter_papers) — it only changes how
    much of each source's ranked list survives the cut.
    """
    import sage_kg
    if not source_weights:
        return sage_kg.filter_papers(papers, queries, cache_dir=cache_dir, drop_frac=drop_frac)

    groups: Dict[str, List[Dict]] = {}
    for p in papers:
        groups.setdefault(p.get("source", "unknown"), []).append(p)

    log.info("=" * 60)
    log.info("STAGE A (source-weighted): base drop_frac=%.2f, weights=%s", drop_frac, source_weights)
    log.info("=" * 60)

    kept_all: List[Dict] = []
    for source, group in groups.items():
        w = float(source_weights.get(source, 1.0))
        group_drop_frac = min(0.95, max(0.0, drop_frac / max(w, 1e-6)))
        log.info("  [%-18s] weight=%.2f -> drop_frac=%.2f (%d papers in)",
                 source, w, group_drop_frac, len(group))
        kept = sage_kg.filter_papers(group, queries, cache_dir=cache_dir, drop_frac=group_drop_frac)
        kept_all.extend(kept)

    log.info("  [source-weighted total] kept %d / %d across %d source groups",
             len(kept_all), len(papers), len(groups))
    return kept_all


# =============================================================================
# Enhance mode: merge new source(s) into an EXISTING graph
# =============================================================================

def enhance_existing_graph(base_graph_path: Path, new_papers: List[Dict]):
    """Extract triples from new_papers only, then fuse them together with the
    existing graph so new content is merged in rather than starting over.

    CAVEAT: this loads the existing .graphml straight via nx.read_graphml and
    hands it to sage_kg.fuse_graphs() alongside the freshly built local graphs
    for new_papers, on the assumption that fuse_graphs() treats every item in
    its `local_graphs` list the same way regardless of origin. That assumption
    hasn't been verified against fuse_graphs()'s actual implementation (only
    its signature was available while writing this). Run one small test
    (a single trial against a small graph) before trusting this on a
    production graph — if node/edge attribute names don't line up with what
    build_local_graph() produces internally, fusion may silently drop or
    duplicate the base graph's content instead of merging it correctly.
    """
    import sage_kg
    import networkx as nx

    if not new_papers:
        log.warning("enhance_existing_graph: no new papers to add, nothing to do")
        return None, None

    log.info("=" * 60)
    log.info("ENHANCE: merging %d new source(s) into %s", len(new_papers), base_graph_path)
    log.info("=" * 60)

    base_G = nx.read_graphml(str(base_graph_path))
    log.info("  base graph: %d nodes, %d edges", base_G.number_of_nodes(), base_G.number_of_edges())

    # Name the merged output after the graph we're actually enhancing, not the
    # module-level default (which is only correct for prostate-cancer builds).
    stem = base_graph_path.stem
    for suffix in ("_latest", "_kg"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    enhance_graph_root = f"{stem}_kg" if not stem.endswith("_kg") else stem

    generate_structured, cc = _structured_generator()
    local_graphs = [base_G]
    total_triples = 0

    for i, p in enumerate(new_papers, 1):
        text = p.get("full_text") or p.get("abstract", "")
        if not text:
            continue
        doc_id = p.get("pmid") or p.get("doi") or p.get("nctid") or f"enhance_doc_{i}"
        try:
            triples = sage_kg.extract_triples(text, generate_structured, conf_threshold=0.5)
        except Exception:
            log.warning("  extraction failed for %s, skipping", doc_id)
            continue
        if not triples:
            continue
        local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
        total_triples += len(triples)
        log.info("  [%d/%d] %s: %d triples", i, len(new_papers), doc_id, len(triples))

    log.info("  new triples extracted: %d (LLM calls: %d, errors: %d)",
             total_triples, cc["count"], cc["errors"])

    G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
    graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=enhance_graph_root)
    log.info("  fused graph: %d nodes, %d edges -> %s", G.number_of_nodes(), G.number_of_edges(), graphml)
    return graphml, G


# =============================================================================
# Stages B/C/D: triple extraction -> local graphs -> fusion (sage_kg.py)
# =============================================================================

def rebuild_from_checkpoint(graph_root: str | None = None):
    """Rebuild the graph from cached triples only (no fetch / full-text / LLM).

    Loads inputs/sage_kg/triples_checkpoint.jsonl -> per-doc local graphs ->
    fusion -> timestamped GraphML. Use after a save failure or to re-fuse with
    changed fusion/serialization settings without re-paying extraction.
    """
    import sage_kg
    log.info("Rebuilding graph from triples checkpoint: %s", TRIPLES_CHECKPOINT)
    if not TRIPLES_CHECKPOINT.exists():
        raise SystemExit(f"No checkpoint at {TRIPLES_CHECKPOINT}")

    docs = {}
    for line in TRIPLES_CHECKPOINT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        docs[rec["doc_id"]] = rec["triples"]
    log.info("  loaded %d cached docs", len(docs))

    local_graphs, total = [], 0
    for doc_id, triples in docs.items():
        if not triples:
            continue
        local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
        total += len(triples)
    log.info("  %d local graphs, %d triples", len(local_graphs), total)

    G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
    graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=graph_root or GRAPH_ROOT)
    return graphml, G


def _structured_generator():
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    cc = {"count": 0, "errors": 0}

    def generate_structured(system_prompt, user_prompt, schema):
        cc["count"] += 1
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                temperature=0.1, max_tokens=4096, response_format=schema,
            )
            if cc["count"] % 50 == 0:
                log.info("  [LLM] %d calls (%d errors)", cc["count"], cc["errors"])
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            cc["errors"] += 1
            track_error("kg_build", user_prompt[:80], str(e))
            raise

    return generate_structured, cc


TRIPLES_CHECKPOINT = CORPUS_DIR / "triples_checkpoint.jsonl"


def _load_checkpoint() -> Dict[str, List[Dict]]:
    """Load already-extracted triples keyed by doc_id (resume support)."""
    done: Dict[str, List[Dict]] = {}
    if not TRIPLES_CHECKPOINT.exists():
        return done
    for line in TRIPLES_CHECKPOINT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            done[rec["doc_id"]] = rec["triples"]
        except Exception:
            continue
    if done:
        log.info("Resume: loaded %d previously-extracted docs from checkpoint", len(done))
    return done


def _append_checkpoint(doc_id: str, triples: List[Dict]) -> None:
    """Append one doc's triples to the JSONL checkpoint (durable per-doc save)."""
    with open(TRIPLES_CHECKPOINT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": doc_id, "triples": triples}) + "\n")


def build_sage_kg(papers: List[Dict], resume: bool = True, graph_root: str | None = None):
    """Extract typed triples -> per-doc local graphs -> fuse -> GraphML.

    Triples are checkpointed to disk per-document (JSONL); a re-run skips docs
    already extracted, so a crash never loses completed LLM work.
    """
    import sage_kg

    log.info("=" * 60)
    log.info("STAGE B+C: Triple extraction + per-document local graphs")
    log.info("=" * 60)
    generate_structured, cc = _structured_generator()

    checkpoint = _load_checkpoint() if resume else {}
    local_graphs, total_triples, start = [], 0, time.time()
    reused = 0

    for i, p in enumerate(papers, 1):
        text = p.get("full_text") or p.get("abstract", "")
        if not text:
            continue
        doc_id = p.get("pmid") or p.get("doi") or p.get("nctid") or f"doc_{i}"

        if doc_id in checkpoint:
            triples = checkpoint[doc_id]
            reused += 1
        else:
            try:
                triples = sage_kg.extract_triples(text, generate_structured, conf_threshold=0.5)
            except Exception:
                continue
            _append_checkpoint(doc_id, triples)  # durable save before anything else

        if not triples:
            continue
        local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
        total_triples += len(triples)
        if i % 10 == 0:
            log.info("  [%d/%d] %d local graphs, %d triples (reused %d)",
                     i, len(papers), len(local_graphs), total_triples, reused)

    log.info("Extraction: %d local graphs, %d triples (c>=0.5), %d reused, in %.1f min",
             len(local_graphs), total_triples, reused, (time.time() - start) / 60)
    log.info("  LLM calls: %d (%d errors)", cc["count"], cc["errors"])

    G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
    graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=graph_root or GRAPH_ROOT)
    return graphml, G


# =============================================================================
# Main
# =============================================================================

def save_error_report():
    report = {"timestamp": datetime.now().isoformat(),
              "total_errors": sum(len(v) for v in errors.values()), "errors": errors}
    (LOG_DIR / "error_report.json").write_text(json.dumps(report, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="keywords/expanded_search_queries.json")
    ap.add_argument("--max-per-query", type=int, default=25)
    ap.add_argument("--drop-frac", type=float, default=0.40)
    ap.add_argument("--no-s2", action="store_true", help="disable Semantic Scholar search")
    ap.add_argument("--limit-papers", type=int, default=0, help="cap papers after fetch (0=all)")
    ap.add_argument("--no-openalex", action="store_true", help="skip OpenAlex citation/venue enrichment")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore + overwrite triples checkpoint (re-extract everything)")
    ap.add_argument("--from-checkpoint", action="store_true",
                    help="rebuild graph from cached triples only — skip fetch/full-text/extract")
    ap.add_argument("--comp-queries", default="keywords/comp_search_queries.json",
                    help="path to computational/ML queries JSON")
    ap.add_argument("--comp-frac", type=float, default=0.15,
                    help="target fraction of corpus from computational queries (0 = disable)")
    ap.add_argument("--manual-docs", default="inputs/sage_kg/manual_docs.json",
                    help="path to JSON file of manually curated docs (textbooks, etc.) injected before Stage A")
    ap.add_argument("--ct-queries", default="keywords/clinicaltrials_queries.json",
                    help="path to ClinicalTrials.gov query JSON (same schema as --queries)")
    ap.add_argument("--ct-max-per-query", type=int, default=25,
                    help="max trials fetched per ClinicalTrials.gov query")
    ap.add_argument("--no-clinicaltrials", action="store_true",
                    help="disable ClinicalTrials.gov search even if --ct-queries exists")
    ap.add_argument("--source-weights", default="",
                    help='JSON string mapping source name -> relative weight, e.g. '
                         '\'{"clinicaltrials": 2.0}\'. Sources: pubmed, semantic_scholar, '
                         'clinicaltrials, manual. Weight 1.0 = default filtering; higher '
                         'keeps relatively more of that source, lower keeps relatively less.')
    ap.add_argument("--base-graph", default="",
                    help="path to an existing .graphml to merge new content into, instead "
                         "of building a fresh graph from scratch (enhance mode)")
    ap.add_argument("--nct-id", default="",
                    help="fetch exactly one ClinicalTrials.gov trial by NCT ID and merge it "
                         "into --base-graph (enhance mode; skips the normal search pipeline)")
    ap.add_argument("--domain", default="",
                    help="research domain, e.g. 'prostate cancer' — used to name the output "
                         "graph file (falls back to the generic default if omitted)")
    args = ap.parse_args()

    if args.fresh and TRIPLES_CHECKPOINT.exists():
        TRIPLES_CHECKPOINT.unlink()
        log.info("--fresh: cleared triples checkpoint")

    import sage_kg
    t0 = time.time()
    log.info("=" * 60)
    log.info("SAGE Knowledge Graph Builder — started %s",
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    graph_root = domain_to_graph_root(args.domain)
    log.info("Output graph name: %s (domain=%r)", graph_root, args.domain or "<unset>")

    # Fast path: enhance mode — merge one specific trial into an existing graph.
    # Used by the UI's "Add Source" box when a clinicaltrials.gov URL is pasted.
    if args.nct_id:
        if not args.base_graph:
            raise SystemExit("--nct-id requires --base-graph (which graph to merge into)")
        trial = clinicaltrials_fetch_by_id(args.nct_id)
        if not trial:
            log.error("Could not fetch or parse trial %s", args.nct_id)
            save_error_report()
            raise SystemExit(f"Failed to fetch {args.nct_id}")
        graphml, G = enhance_existing_graph(Path(args.base_graph), [trial])
        log.info("=" * 60)
        log.info("COMPLETE (enhance: single trial) in %.1f min", (time.time() - t0) / 60)
        log.info("  GraphML: %s", graphml)
        log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
        log.info("=" * 60)
        save_error_report()
        return

    # Fast path: rebuild the graph straight from cached triples (no network/LLM).
    if args.from_checkpoint:
        graphml, G = rebuild_from_checkpoint(graph_root=graph_root)
        log.info("=" * 60)
        log.info("COMPLETE (from checkpoint) in %.1f min", (time.time() - t0) / 60)
        log.info("  GraphML: %s", graphml)
        log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
        log.info("=" * 60)
        return

    try:
        # Phase 2: search (title + abstract + identifiers)
        papers = fetch_all_papers(args.queries, args.max_per_query, use_s2=not args.no_s2,
                                  comp_queries_file=args.comp_queries, comp_frac=args.comp_frac)
        if args.limit_papers:
            papers = papers[:args.limit_papers]
            log.info("Capped to %d papers (testing)", len(papers))

        # Phase 2c: ClinicalTrials.gov — additive, on top of the PubMed/S2 search
        # above. Does not replace or narrow it; results are merged in before
        # Stage A filtering, same as manual docs below.
        if not args.no_clinicaltrials:
            ct_trials = fetch_all_clinicaltrials(args.ct_queries, args.ct_max_per_query)
            if ct_trials:
                papers.extend(ct_trials)
                papers = deduplicate(papers)

        # Manual docs (textbooks, etc.) — injected with full_text pre-set, bypass Phase 3
        manual = load_manual_docs(args.manual_docs)
        if manual:
            papers.extend(manual)
            papers = deduplicate(papers)

        # Enrich with OpenAlex citations + venue (needed by the relevance filter).
        if not args.no_openalex:
            papers = enrich_openalex(papers)

        # Stage A: relevance filter BEFORE full-text retrieval, so we only
        # download the full text of papers we actually keep (no wasted PMC /
        # Unpaywall fetches on low-relevance / low-venue papers).
        with open(args.queries) as f:
            queries = json.load(f)
        source_weights = json.loads(args.source_weights) if args.source_weights else None
        papers = filter_papers_weighted(papers, queries, cache_dir=EMBED_CACHE_DIR,
                                        drop_frac=args.drop_frac, source_weights=source_weights)
        if not papers:
            log.error("No papers survived filtering.")
            save_error_report()
            return

        # Phase 3: full-text retrieval (only for the kept papers)
        papers = attach_fulltext(papers, ncbi_key=NCBI_API_KEY)

        (CORPUS_DIR / "paper_metadata.json").write_text(json.dumps(papers, indent=2))
        log.info("Metadata saved: %d kept papers (full text retrieved)", len(papers))

        graphml, G = build_sage_kg(papers, resume=not args.fresh, graph_root=graph_root)

        log.info("=" * 60)
        log.info("COMPLETE in %.1f min", (time.time() - t0) / 60)
        log.info("  GraphML: %s", graphml)
        log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
        log.info("=" * 60)
    except Exception as e:
        log.error("Fatal: %s\n%s", e, traceback.format_exc())
        save_error_report()
        raise
    save_error_report()


if __name__ == "__main__":
    main()



# """
# Build a biomedical knowledge graph (SAGE pipeline) from a topic's literature.

# End-to-end:
#   Phase 2 (search) : PubMed (NCBI E-utils) + Semantic Scholar (keyless).
#   Phase 3 (full text): for each paper try, in order,
#                        PMC efetch -> Europe PMC full-text XML -> Unpaywall OA PDF
#                        (3-tier parse) -> abstract fallback.
#   Stage A-D (sage_kg.py): SapBERT relevance filter -> typed-triple extraction
#                        (GPT-4o-mini, c>=0.5) -> per-document local graphs ->
#                        BGE-Large fusion (tau=0.8) -> prune -> GraphML.

# Input  : keywords/expanded_search_queries.json  (from expand_keywords.py)
# Output : outputs/sage_kg/prostate_cancer_kg.graphml  (+ typed node/edge JSON)

# Run Phase 1 first:  python expand_keywords.py
# Then this:          python build_kg.py
# """

# import argparse
# import json
# import logging
# import os
# import sys
# import time
# import traceback
# from datetime import datetime
# from pathlib import Path
# from typing import Dict, List
# from xml.etree import ElementTree as ET

# import requests

# # =============================================================================
# # Config / logging / keys
# # =============================================================================

# LOG_DIR = Path("logs")
# LOG_DIR.mkdir(parents=True, exist_ok=True)
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s | %(levelname)-7s | %(message)s",
#     handlers=[logging.FileHandler(LOG_DIR / "build_kg.log", encoding="utf-8"),
#               logging.StreamHandler(sys.stdout)],
# )
# log = logging.getLogger("build_kg")

# if Path(".env").exists():
#     for line in open(".env"):
#         if "=" in line and not line.startswith("#"):
#             k, v = line.strip().split("=", 1)
#             os.environ.setdefault(k, v)

# OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# _ncbi = os.environ.get("NCBI_API_KEY", "")
# _s2 = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
# NCBI_API_KEY = _ncbi if _ncbi and not _ncbi.startswith("your-") and len(_ncbi) > 5 else ""
# S2_API_KEY = _s2 if _s2 and not _s2.startswith("your-") and len(_s2) > 5 else ""
# UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "jduke99@gmail.com")

# CORPUS_DIR = Path("inputs/sage_kg")
# OUTPUT_DIR = Path("outputs/sage_kg")
# EMBED_CACHE_DIR = CORPUS_DIR / "embed_cache"
# for d in (CORPUS_DIR, OUTPUT_DIR, EMBED_CACHE_DIR):
#     d.mkdir(parents=True, exist_ok=True)

# NCBI = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
# S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
# OPENALEX = "https://api.openalex.org/works"
# OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "jduke99@gmail.com")
# GRAPH_ROOT = "prostate_cancer_kg"

# log.info("Keys: OPENAI=%s NCBI=%s S2=%s",
#          "SET" if OPENAI_API_KEY else "MISSING",
#          "SET" if NCBI_API_KEY else "none", "SET" if S2_API_KEY else "keyless")

# errors: Dict[str, List] = {"pubmed": [], "s2": [], "clinicaltrials": [], "fulltext": [], "kg_build": []}


# def track_error(source: str, ctx: str, msg) -> None:
#     errors[source].append({"ctx": ctx[:120], "error": str(msg), "time": datetime.now().isoformat()})
#     log.error("[%s] %s", source.upper(), msg)


# # =============================================================================
# # Phase 2a: PubMed search (NCBI E-utils)
# # =============================================================================

# def pubmed_search(query: str, max_results: int = 25, api_key: str = "") -> List[Dict]:
#     """Search PubMed, return records with pmid/pmcid/doi/title/abstract/year."""
#     papers = []
#     try:
#         params = {"db": "pubmed", "term": query, "retmax": max_results,
#                   "retmode": "json", "sort": "relevance"}
#         if api_key:
#             params["api_key"] = api_key
#         r = requests.get(f"{NCBI}/esearch.fcgi", params=params, timeout=15)
#         r.raise_for_status()
#         ids = r.json().get("esearchresult", {}).get("idlist", [])
#         if not ids:
#             return papers

#         time.sleep(0.34)
#         fp = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
#         if api_key:
#             fp["api_key"] = api_key
#         fr = requests.get(f"{NCBI}/efetch.fcgi", params=fp, timeout=30)
#         fr.raise_for_status()
#         root = ET.fromstring(fr.content)

#         for art in root.findall(".//PubmedArticle"):
#             te = art.find(".//ArticleTitle")
#             title = te.text if te is not None and te.text else ""
#             abstract = " ".join(a.text for a in art.findall(".//AbstractText") if a.text)
#             pe = art.find(".//PMID")
#             pmid = pe.text if pe is not None else ""
#             ye = art.find(".//PubDate/Year")
#             year = ye.text if ye is not None else ""
#             # Journal title (for venue / impact-factor scoring in Stage A).
#             je = art.find(".//Journal/Title")
#             jae = art.find(".//Journal/ISOAbbreviation")
#             journal = (je.text if je is not None and je.text else
#                        (jae.text if jae is not None and jae.text else ""))
#             pmcid, doi = "", ""
#             for aid in art.findall(".//ArticleId"):
#                 if aid.get("IdType") == "pmc":
#                     pmcid = aid.text or ""
#                 elif aid.get("IdType") == "doi":
#                     doi = aid.text or ""
#             if title or abstract:
#                 papers.append({
#                     "title": title, "abstract": abstract, "year": year,
#                     "pmid": pmid, "pmcid": pmcid, "doi": doi, "journal": journal,
#                     "citationCount": 0, "source": "pubmed", "query": query,
#                 })
#     except requests.exceptions.HTTPError as e:
#         track_error("pubmed", query, f"HTTP {e.response.status_code}")
#     except Exception as e:
#         track_error("pubmed", query, str(e))
#     return papers


# # =============================================================================
# # Phase 2b: Semantic Scholar search (keyless or keyed)
# # =============================================================================

# _s2_state = {"consecutive_403s": 0}


# def s2_search(query: str, max_results: int = 25, api_key: str = "") -> List[Dict]:
#     """Search Semantic Scholar. Keyless works but is throttled/shared; on repeated
#     403/429 we stop calling S2 for the rest of the run."""
#     papers = []
#     if _s2_state["consecutive_403s"] >= 5:
#         return papers
#     try:
#         headers = {"x-api-key": api_key} if api_key else None
#         params = {"query": query, "limit": min(max_results, 100),
#                   "fields": "title,abstract,year,venue,externalIds,url,citationCount,influentialCitationCount"}
#         time.sleep(0.2 if api_key else 1.1)
#         r = requests.get(S2_SEARCH, params=params, headers=headers, timeout=15)

#         retries = 0
#         while r.status_code == 429 and retries < 3:
#             wait = 5 * (retries + 1)
#             log.warning("  [S2] 429, retry %d/3 after %ds", retries + 1, wait)
#             time.sleep(wait)
#             r = requests.get(S2_SEARCH, params=params, headers=headers, timeout=15)
#             retries += 1

#         if r.status_code == 403:
#             _s2_state["consecutive_403s"] += 1
#             return papers
#         r.raise_for_status()
#         _s2_state["consecutive_403s"] = 0

#         for it in r.json().get("data", []):
#             title = it.get("title", "")
#             abstract = it.get("abstract") or ""
#             doi = (it.get("externalIds") or {}).get("DOI", "")
#             pmid = (it.get("externalIds") or {}).get("PubMed", "")
#             pmcid = (it.get("externalIds") or {}).get("PMCID", "")
#             if title or abstract:
#                 papers.append({
#                     "title": title, "abstract": abstract, "year": str(it.get("year", "")),
#                     "pmid": pmid, "pmcid": pmcid, "doi": doi,
#                     "journal": it.get("venue", ""),
#                     "citationCount": int(it.get("citationCount", 0) or 0),
#                     "influentialCitationCount": int(it.get("influentialCitationCount", 0) or 0),
#                     "source": "semantic_scholar", "query": query,
#                 })
#     except requests.exceptions.HTTPError as e:
#         track_error("s2", query, f"HTTP {e.response.status_code}")
#     except Exception as e:
#         track_error("s2", query, str(e))
#     return papers


# # =============================================================================
# # Phase 2c: ClinicalTrials.gov search (API v2, keyless)
# # =============================================================================

# CTGOV_API = "https://clinicaltrials.gov/api/v2/studies"


# import re as _re

# NCT_ID_RE = _re.compile(r"NCT\d{8}", _re.IGNORECASE)


# def extract_nct_id(text: str) -> str:
#     """Pull an NCT ID out of a clinicaltrials.gov URL or bare string, e.g.
#     'https://clinicaltrials.gov/study/NCT01234567' -> 'NCT01234567'. '' if none."""
#     m = NCT_ID_RE.search(text or "")
#     return m.group(0).upper() if m else ""


# def _parse_ct_study(s: Dict, query: str = "") -> Dict | None:
#     """Shared parser: one ClinicalTrials.gov API v2 study object -> a paper-shaped
#     dict (see module docstring for why this shape). Returns None if there isn't
#     enough content to be useful (extract_triples needs real text to work with)."""
#     proto = s.get("protocolSection", {})
#     ident = proto.get("identificationModule", {})
#     desc = proto.get("descriptionModule", {})
#     cond = proto.get("conditionsModule", {})
#     arms = proto.get("armsInterventionsModule", {})
#     outcomes = proto.get("outcomesModule", {})
#     status = proto.get("statusModule", {})
#     sponsor = proto.get("sponsorCollaboratorsModule", {})

#     nct_id = ident.get("nctId", "")
#     title = ident.get("briefTitle", "") or ident.get("officialTitle", "")
#     summary = desc.get("briefSummary", "") or ""
#     detailed = desc.get("detailedDescription", "") or ""
#     conditions = ", ".join(cond.get("conditions", []) or [])
#     interventions = "; ".join(
#         f"{iv.get('type', '')}: {iv.get('name', '')}"
#         for iv in (arms.get("interventions", []) or [])
#     )
#     primary_outcomes = "; ".join(
#         o.get("measure", "") for o in (outcomes.get("primaryOutcomes", []) or [])
#     )
#     start_date = (status.get("startDateStruct", {}) or {}).get("date", "")
#     year = start_date[:4] if start_date else ""
#     lead_sponsor = (sponsor.get("leadSponsor", {}) or {}).get("name", "")

#     full_text_parts = [
#         f"Title: {title}",
#         f"Conditions: {conditions}" if conditions else "",
#         f"Summary: {summary}" if summary else "",
#         f"Detailed description: {detailed}" if detailed else "",
#         f"Interventions: {interventions}" if interventions else "",
#         f"Primary outcomes: {primary_outcomes}" if primary_outcomes else "",
#     ]
#     full_text = "\n".join(part for part in full_text_parts if part)

#     if not (title and (summary or detailed)):
#         return None  # not enough content to extract triples from

#     return {
#         "title": title,
#         "abstract": summary or full_text[:600],
#         "full_text": full_text,
#         "text_source": "clinicaltrials",
#         "year": year,
#         "pmid": "",
#         "pmcid": "",
#         "doi": "",
#         "nctid": nct_id,
#         "journal": lead_sponsor,  # reused as a loose "venue" signal for Stage A
#         "citationCount": 0,
#         "source": "clinicaltrials",
#         "query": query,
#         "query_type": "clinical_trial",
#     }


# def clinicaltrials_search(query: str, max_results: int = 25) -> List[Dict]:
#     """Search ClinicalTrials.gov (API v2) and return records shaped like the
#     paper dicts used elsewhere in the pipeline.

#     Full text is pre-populated from the trial's summary / detailed description /
#     interventions / primary outcomes, so these records bypass Phase 3 (full-text
#     retrieval) the same way manual_docs entries do — they go straight into
#     Stage A filtering with text already attached.
#     """
#     papers = []
#     try:
#         params = {
#             "query.term": query,
#             "pageSize": min(max_results, 100),
#             "format": "json",
#         }
#         r = requests.get(CTGOV_API, params=params, timeout=20)
#         r.raise_for_status()
#         for s in r.json().get("studies", []):
#             p = _parse_ct_study(s, query=query)
#             if p:
#                 papers.append(p)
#     except requests.exceptions.HTTPError as e:
#         track_error("clinicaltrials", query, f"HTTP {e.response.status_code}")
#     except Exception as e:
#         track_error("clinicaltrials", query, str(e))
#     return papers


# def clinicaltrials_fetch_by_id(nct_id: str) -> Dict | None:
#     """Fetch a single trial directly by NCT ID (GET /studies/{nctId}) — used
#     when a person pastes a specific clinicaltrials.gov URL into 'Add Source'
#     rather than running a broad search."""
#     try:
#         r = requests.get(f"{CTGOV_API}/{nct_id}", params={"format": "json"}, timeout=20)
#         r.raise_for_status()
#         return _parse_ct_study(r.json(), query=f"direct:{nct_id}")
#     except requests.exceptions.HTTPError as e:
#         track_error("clinicaltrials", nct_id, f"HTTP {e.response.status_code}")
#         return None
#     except Exception as e:
#         track_error("clinicaltrials", nct_id, str(e))
#         return None


# def fetch_all_clinicaltrials(queries_file: str, max_per_query: int) -> List[Dict]:
#     """Run a list of queries against ClinicalTrials.gov and return combined
#     trial records. queries_file uses the same schema as the main PubMed
#     queries file: a JSON list of {category, subcategory, query, rationale}.

#     This is additive — it does not replace or narrow the default PubMed +
#     Semantic Scholar search in fetch_all_papers(); results are merged in
#     before Stage A filtering (see main()).
#     """
#     p = Path(queries_file)
#     if not p.exists():
#         log.warning("ClinicalTrials queries file not found: %s (skipping)", queries_file)
#         return []
#     with open(p) as f:
#         queries = json.load(f)

#     log.info("=" * 60)
#     log.info("PHASE 2c: Search ClinicalTrials.gov (%d queries, <=%d/query)",
#              len(queries), max_per_query)
#     log.info("=" * 60)

#     all_trials = []
#     for i, q in enumerate(queries, 1):
#         query = q["query"]
#         label = f"{q.get('category', '')}/{q.get('subcategory', '')}".strip("/")
#         trials = clinicaltrials_search(query, max_per_query)
#         all_trials.extend(trials)
#         log.info("[ct %d/%d] %-38s trials=%d", i, len(queries), label[:38], len(trials))

#     log.info("ClinicalTrials.gov: %d raw trial records", len(all_trials))
#     return all_trials


# # =============================================================================
# # Manual documents (textbooks, preprints, etc.)
# # =============================================================================

# def load_manual_docs(path: str) -> List[Dict]:
#     """Load manually curated documents from a JSON file.

#     Each entry must have at least "title" and "full_text". Optional fields:
#     "abstract", "year", "source", "journal". Documents are injected directly
#     into the pipeline after search/dedup — they bypass Phase 3 (full-text
#     retrieval) and go straight through Stage A–D.

#     Example entry:
#         {"title": "Prostate Cancer", "source": "statpearls",
#          "year": "2024", "full_text": "Prostate cancer is ..."}
#     """
#     p = Path(path)
#     if not p.exists():
#         return []
#     docs = json.load(open(p))
#     out = []
#     for d in docs:
#         if not d.get("title") or not d.get("full_text"):
#             log.warning("  [manual_docs] skipping entry missing title or full_text")
#             continue
#         out.append({
#             "title": d["title"],
#             "abstract": d.get("abstract", d["full_text"][:600]),
#             "full_text": d["full_text"],
#             "text_source": d.get("source", "manual"),
#             "year": str(d.get("year", "")),
#             "pmid": d.get("pmid", ""),
#             "pmcid": d.get("pmcid", ""),
#             "doi": d.get("doi", ""),
#             "journal": d.get("journal", ""),
#             "citationCount": 0,
#             "source": d.get("source", "manual"),
#             "query": "manual",
#             "query_type": "textbook",
#         })
#     log.info("Manual docs loaded: %d documents from %s", len(out), path)
#     return out


# # =============================================================================
# # Dedup + fetch orchestration
# # =============================================================================

# def deduplicate(papers: List[Dict]) -> List[Dict]:
#     """Dedup by DOI, then PMID, then NCT ID, then normalized title. Merge citation counts."""
#     by_key: Dict[str, Dict] = {}
#     order: List[str] = []
#     dupes = 0
#     for p in papers:
#         key = ((p.get("doi") or "").lower() or (p.get("pmid") or "")
#                or (p.get("nctid") or "") or p.get("title", "").lower().strip())
#         if not key:
#             continue
#         if key not in by_key:
#             by_key[key] = p
#             order.append(key)
#         else:
#             dupes += 1
#             ex = by_key[key]
#             # keep richer record: prefer one with more identifiers / citations
#             ex["citationCount"] = max(ex.get("citationCount", 0), p.get("citationCount", 0))
#             for f in ("pmcid", "doi", "pmid", "nctid", "abstract", "journal"):
#                 if not ex.get(f) and p.get(f):
#                     ex[f] = p[f]
#     log.info("Dedup: %d removed, %d unique", dupes, len(order))
#     return [by_key[k] for k in order]


# def enrich_openalex(papers: List[Dict]) -> List[Dict]:
#     """Fill citationCount + journal from OpenAlex (free, no key) for papers with a DOI.

#     OpenAlex replaces Semantic Scholar for the citation/venue signal used by the
#     Stage A filter. Batched by DOI via the works filter endpoint (50/req).
#     """
#     log.info("=" * 60)
#     log.info("OpenAlex enrichment (citations + venue)")
#     log.info("=" * 60)
#     with_doi = [p for p in papers if p.get("doi")]
#     if not with_doi:
#         log.info("  no DOIs to enrich")
#         return papers
#     by_doi = {p["doi"].lower(): p for p in with_doi}

#     enriched = 0
#     dois = list(by_doi.keys())
#     for s in range(0, len(dois), 50):
#         batch = dois[s:s + 50]
#         filt = "doi:" + "|".join(batch)  # OpenAlex OR-filter on DOIs
#         try:
#             time.sleep(0.15)
#             r = requests.get(OPENALEX, timeout=30, params={
#                 "filter": filt, "per_page": 50, "mailto": OPENALEX_MAILTO,
#                 "select": "doi,cited_by_count,primary_location",
#             })
#             if r.status_code != 200:
#                 continue
#             for w in r.json().get("results", []):
#                 wdoi = (w.get("doi") or "").lower().replace("https://doi.org/", "")
#                 p = by_doi.get(wdoi)
#                 if not p:
#                     continue
#                 p["citationCount"] = int(w.get("cited_by_count", 0) or 0)
#                 src = (w.get("primary_location") or {}).get("source") or {}
#                 if src.get("display_name") and not p.get("journal"):
#                     p["journal"] = src["display_name"]
#                 enriched += 1
#         except Exception as e:
#             log.debug("  [OpenAlex] batch failed: %s", e)
#         if (s // 50) % 5 == 0:
#             log.info("  enriched %d/%d", min(s + 50, len(dois)), len(dois))

#     cited = sum(1 for p in papers if p.get("citationCount", 0) > 0)
#     log.info("  enriched %d papers; %d now have citationCount>0", enriched, cited)
#     return papers


# def fetch_all_papers(queries_file: str, max_per_query: int, use_s2: bool,
#                      comp_queries_file: str = "", comp_frac: float = 0.0) -> List[Dict]:
#     with open(queries_file) as f:
#         queries = json.load(f)
#     comp_queries = []
#     if comp_frac > 0 and comp_queries_file and Path(comp_queries_file).exists():
#         with open(comp_queries_file) as f:
#             comp_queries = json.load(f)
#     elif comp_frac > 0 and comp_queries_file:
#         log.warning("comp-queries file not found: %s (skipping computational papers)", comp_queries_file)
#     log.info("=" * 60)
#     log.info("PHASE 2: Search PubMed%s (%d bio + %d comp queries, <=%d/query)",
#              " + Semantic Scholar" if use_s2 else "",
#              len(queries), len(comp_queries), max_per_query)
#     log.info("=" * 60)

#     all_papers, start = [], time.time()
#     for i, q in enumerate(queries, 1):
#         query = q["query"]
#         label = f"{q.get('category','')}/{q.get('subcategory','')}".strip("/")
#         pm = pubmed_search(query, max_per_query, api_key=NCBI_API_KEY)
#         s2 = s2_search(query, max_per_query, api_key=S2_API_KEY) if use_s2 else []
#         for p in pm + s2:
#             p["query_type"] = "biomedical"
#         all_papers.extend(pm)
#         all_papers.extend(s2)
#         log.info("[%d/%d] %-38s PubMed=%d S2=%d", i, len(queries), label[:38], len(pm), len(s2))

#     comp_raw = []
#     for i, q in enumerate(comp_queries, 1):
#         query = q["query"]
#         label = f"comp/{q.get('subcategory', '')}"[:38]
#         pm = pubmed_search(query, max_per_query, api_key=NCBI_API_KEY)
#         s2 = s2_search(query, max_per_query, api_key=S2_API_KEY) if use_s2 else []
#         for p in pm + s2:
#             p["query_type"] = "computational"
#         comp_raw.extend(pm)
#         comp_raw.extend(s2)
#         log.info("[comp %d/%d] %-38s PubMed=%d S2=%d", i, len(comp_queries), label, len(pm), len(s2))
#     all_papers.extend(comp_raw)

#     log.info("Search done in %.1f min, %d raw papers", (time.time() - start) / 60, len(all_papers))
#     deduped = deduplicate(all_papers)

#     if comp_frac > 0 and comp_queries:
#         bio = [p for p in deduped if p.get("query_type") != "computational"]
#         comp = [p for p in deduped if p.get("query_type") == "computational"]
#         max_comp = max(1, round(len(bio) * comp_frac / max(1 - comp_frac, 0.01)))
#         if len(comp) > max_comp:
#             comp = comp[:max_comp]
#         log.info("Comp fraction %.0f%%: %d comp papers, %d bio papers (total %d)",
#                  comp_frac * 100, len(comp), len(bio), len(bio) + len(comp))
#         deduped = bio + comp

#     return deduped


# # =============================================================================
# # Phase 3: Full-text retrieval
# #   PMC efetch -> Europe PMC full-text XML -> Unpaywall OA PDF -> abstract
# # =============================================================================

# def _xml_text(elem) -> str:
#     return " ".join(t.strip() for t in elem.itertext() if t and t.strip())


# def fetch_pmc_efetch(pmcid: str, api_key: str = "") -> str:
#     """NCBI PMC full text via efetch db=pmc. '' on failure."""
#     if not pmcid:
#         return ""
#     try:
#         time.sleep(0.34)
#         params = {"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"}
#         if api_key:
#             params["api_key"] = api_key
#         r = requests.get(f"{NCBI}/efetch.fcgi", params=params, timeout=30)
#         r.raise_for_status()
#         body = ET.fromstring(r.content).find(".//body")
#         if body is None:
#             return ""
#         text = _xml_text(body)
#         return text if len(text) > 500 else ""
#     except Exception as e:
#         log.debug("  [PMC efetch] %s: %s", pmcid, e)
#         return ""


# def fetch_epmc_xml(pmcid: str) -> str:
#     """Europe PMC full-text XML. '' on failure."""
#     if not pmcid:
#         return ""
#     try:
#         time.sleep(0.2)
#         r = requests.get(f"{EPMC}/{pmcid}/fullTextXML", timeout=30)
#         if r.status_code != 200:
#             return ""
#         body = ET.fromstring(r.content).find(".//body")
#         if body is None:
#             return ""
#         text = _xml_text(body)
#         return text if len(text) > 500 else ""
#     except Exception as e:
#         log.debug("  [EPMC XML] %s: %s", pmcid, e)
#         return ""


# def _extract_pdf_text(pdf_bytes: bytes) -> str:
#     """3-tier PDF text extraction: pdfplumber -> PyMuPDF -> pypdf."""
#     import io
#     try:
#         import pdfplumber
#         with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
#             t = "\n".join(p.extract_text() or "" for p in pdf.pages)
#         if len(t.strip()) > 500:
#             return t
#     except Exception:
#         pass
#     try:
#         import fitz
#         doc = fitz.open(stream=pdf_bytes, filetype="pdf")
#         t = "\n".join(pg.get_text() for pg in doc)
#         if len(t.strip()) > 500:
#             return t
#     except Exception:
#         pass
#     try:
#         from pypdf import PdfReader
#         t = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf_bytes)).pages)
#         if len(t.strip()) > 500:
#             return t
#     except Exception:
#         pass
#     return ""


# def fetch_unpaywall_pdf_text(doi: str) -> str:
#     """Resolve OA PDF via Unpaywall, download, extract text. '' on failure."""
#     if not doi:
#         return ""
#     try:
#         time.sleep(0.2)
#         r = requests.get(f"https://api.unpaywall.org/v2/{doi}",
#                          params={"email": UNPAYWALL_EMAIL}, timeout=15)
#         if r.status_code != 200:
#             return ""
#         loc = (r.json() or {}).get("best_oa_location") or {}
#         pdf_url = loc.get("url_for_pdf")
#         if not pdf_url:
#             return ""
#         pr = requests.get(pdf_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
#         if pr.status_code != 200 or not pr.content[:5].startswith(b"%PDF"):
#             return ""
#         return _extract_pdf_text(pr.content)
#     except Exception as e:
#         log.debug("  [Unpaywall] %s: %s", doi, e)
#         return ""


# def attach_fulltext(papers: List[Dict], ncbi_key: str = "") -> List[Dict]:
#     """Populate full_text + text_source per paper via the fallback chain."""
#     log.info("=" * 60)
#     log.info("PHASE 3: Full-text retrieval (PMC -> EuropePMC -> Unpaywall -> abstract)")
#     log.info("=" * 60)
#     counts = {"pmc_efetch": 0, "epmc_xml": 0, "unpaywall_pdf": 0, "abstract": 0, "pre_fetched": 0}
#     for i, p in enumerate(papers, 1):
#         if p.get("full_text") and p.get("text_source") not in ("abstract", "", None):
#             counts["pre_fetched"] += 1
#             continue
#         text, source = "", "abstract"
#         pmcid = p.get("pmcid", "")
#         if pmcid:
#             text = fetch_pmc_efetch(pmcid, api_key=ncbi_key)
#             if text:
#                 source = "pmc_efetch"
#             else:
#                 text = fetch_epmc_xml(pmcid)
#                 if text:
#                     source = "epmc_xml"
#         if not text and p.get("doi"):
#             text = fetch_unpaywall_pdf_text(p["doi"])
#             if text:
#                 source = "unpaywall_pdf"
#         if not text:
#             text = f"{p.get('title','')}\n{p.get('abstract','')}".strip()
#             source = "abstract"
#         p["full_text"], p["text_source"] = text, source
#         counts[source] += 1
#         if i % 50 == 0:
#             log.info("  [%d/%d] %s", i, len(papers), counts)
#     log.info("Full-text sources: %s", counts)
#     return papers


# def filter_papers_weighted(papers: List[Dict], queries: List[Dict], cache_dir: Path,
#                            drop_frac: float, source_weights: Dict[str, float] | None = None) -> List[Dict]:
#     """Run Stage A relevance filtering (sage_kg.filter_papers) per-source group
#     instead of globally, so specific sources can be prioritized relative to
#     others when building a graph.

#     source_weights maps a paper's "source" field (e.g. "clinicaltrials",
#     "pubmed", "semantic_scholar") to a relative weight. Weight 1.0 = default
#     drop_frac for that source. Weight 2.0 halves that source's drop_frac
#     (keeps roughly 2x as much of it, relative to the baseline); weight 0.5
#     doubles it (drops more). Sources not listed default to weight 1.0.

#     This does not change the underlying relevance scoring (still SapBERT +
#     venue + citations, same as sage_kg.filter_papers) — it only changes how
#     much of each source's ranked list survives the cut.
#     """
#     import sage_kg
#     if not source_weights:
#         return sage_kg.filter_papers(papers, queries, cache_dir=cache_dir, drop_frac=drop_frac)

#     groups: Dict[str, List[Dict]] = {}
#     for p in papers:
#         groups.setdefault(p.get("source", "unknown"), []).append(p)

#     log.info("=" * 60)
#     log.info("STAGE A (source-weighted): base drop_frac=%.2f, weights=%s", drop_frac, source_weights)
#     log.info("=" * 60)

#     kept_all: List[Dict] = []
#     for source, group in groups.items():
#         w = float(source_weights.get(source, 1.0))
#         group_drop_frac = min(0.95, max(0.0, drop_frac / max(w, 1e-6)))
#         log.info("  [%-18s] weight=%.2f -> drop_frac=%.2f (%d papers in)",
#                  source, w, group_drop_frac, len(group))
#         kept = sage_kg.filter_papers(group, queries, cache_dir=cache_dir, drop_frac=group_drop_frac)
#         kept_all.extend(kept)

#     log.info("  [source-weighted total] kept %d / %d across %d source groups",
#              len(kept_all), len(papers), len(groups))
#     return kept_all


# # =============================================================================
# # Enhance mode: merge new source(s) into an EXISTING graph
# # =============================================================================

# def enhance_existing_graph(base_graph_path: Path, new_papers: List[Dict]):
#     """Extract triples from new_papers only, then fuse them together with the
#     existing graph so new content is merged in rather than starting over.

#     CAVEAT: this loads the existing .graphml straight via nx.read_graphml and
#     hands it to sage_kg.fuse_graphs() alongside the freshly built local graphs
#     for new_papers, on the assumption that fuse_graphs() treats every item in
#     its `local_graphs` list the same way regardless of origin. That assumption
#     hasn't been verified against fuse_graphs()'s actual implementation (only
#     its signature was available while writing this). Run one small test
#     (a single trial against a small graph) before trusting this on a
#     production graph — if node/edge attribute names don't line up with what
#     build_local_graph() produces internally, fusion may silently drop or
#     duplicate the base graph's content instead of merging it correctly.
#     """
#     import sage_kg
#     import networkx as nx

#     if not new_papers:
#         log.warning("enhance_existing_graph: no new papers to add, nothing to do")
#         return None, None

#     log.info("=" * 60)
#     log.info("ENHANCE: merging %d new source(s) into %s", len(new_papers), base_graph_path)
#     log.info("=" * 60)

#     base_G = nx.read_graphml(str(base_graph_path))
#     log.info("  base graph: %d nodes, %d edges", base_G.number_of_nodes(), base_G.number_of_edges())

#     # Name the merged output after the graph we're actually enhancing, not the
#     # module-level default (which is only correct for prostate-cancer builds).
#     stem = base_graph_path.stem
#     for suffix in ("_latest", "_kg"):
#         if stem.endswith(suffix):
#             stem = stem[: -len(suffix)]
#     enhance_graph_root = f"{stem}_kg" if not stem.endswith("_kg") else stem

#     generate_structured, cc = _structured_generator()
#     local_graphs = [base_G]
#     total_triples = 0

#     for i, p in enumerate(new_papers, 1):
#         text = p.get("full_text") or p.get("abstract", "")
#         if not text:
#             continue
#         doc_id = p.get("pmid") or p.get("doi") or p.get("nctid") or f"enhance_doc_{i}"
#         try:
#             triples = sage_kg.extract_triples(text, generate_structured, conf_threshold=0.5)
#         except Exception:
#             log.warning("  extraction failed for %s, skipping", doc_id)
#             continue
#         if not triples:
#             continue
#         local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
#         total_triples += len(triples)
#         log.info("  [%d/%d] %s: %d triples", i, len(new_papers), doc_id, len(triples))

#     log.info("  new triples extracted: %d (LLM calls: %d, errors: %d)",
#              total_triples, cc["count"], cc["errors"])

#     G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
#     graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=enhance_graph_root)
#     log.info("  fused graph: %d nodes, %d edges -> %s", G.number_of_nodes(), G.number_of_edges(), graphml)
#     return graphml, G


# # =============================================================================
# # Stages B/C/D: triple extraction -> local graphs -> fusion (sage_kg.py)
# # =============================================================================

# def rebuild_from_checkpoint():
#     """Rebuild the graph from cached triples only (no fetch / full-text / LLM).

#     Loads inputs/sage_kg/triples_checkpoint.jsonl -> per-doc local graphs ->
#     fusion -> timestamped GraphML. Use after a save failure or to re-fuse with
#     changed fusion/serialization settings without re-paying extraction.
#     """
#     import sage_kg
#     log.info("Rebuilding graph from triples checkpoint: %s", TRIPLES_CHECKPOINT)
#     if not TRIPLES_CHECKPOINT.exists():
#         raise SystemExit(f"No checkpoint at {TRIPLES_CHECKPOINT}")

#     docs = {}
#     for line in TRIPLES_CHECKPOINT.read_text(encoding="utf-8").splitlines():
#         line = line.strip()
#         if not line:
#             continue
#         rec = json.loads(line)
#         docs[rec["doc_id"]] = rec["triples"]
#     log.info("  loaded %d cached docs", len(docs))

#     local_graphs, total = [], 0
#     for doc_id, triples in docs.items():
#         if not triples:
#             continue
#         local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
#         total += len(triples)
#     log.info("  %d local graphs, %d triples", len(local_graphs), total)

#     G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
#     graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=GRAPH_ROOT)
#     return graphml, G


# def _structured_generator():
#     from openai import OpenAI
#     client = OpenAI(api_key=OPENAI_API_KEY)
#     cc = {"count": 0, "errors": 0}

#     def generate_structured(system_prompt, user_prompt, schema):
#         cc["count"] += 1
#         try:
#             resp = client.chat.completions.create(
#                 model="gpt-4o-mini",
#                 messages=[{"role": "system", "content": system_prompt},
#                           {"role": "user", "content": user_prompt}],
#                 temperature=0.1, max_tokens=4096, response_format=schema,
#             )
#             if cc["count"] % 50 == 0:
#                 log.info("  [LLM] %d calls (%d errors)", cc["count"], cc["errors"])
#             return json.loads(resp.choices[0].message.content)
#         except Exception as e:
#             cc["errors"] += 1
#             track_error("kg_build", user_prompt[:80], str(e))
#             raise

#     return generate_structured, cc


# TRIPLES_CHECKPOINT = CORPUS_DIR / "triples_checkpoint.jsonl"


# def _load_checkpoint() -> Dict[str, List[Dict]]:
#     """Load already-extracted triples keyed by doc_id (resume support)."""
#     done: Dict[str, List[Dict]] = {}
#     if not TRIPLES_CHECKPOINT.exists():
#         return done
#     for line in TRIPLES_CHECKPOINT.read_text(encoding="utf-8").splitlines():
#         line = line.strip()
#         if not line:
#             continue
#         try:
#             rec = json.loads(line)
#             done[rec["doc_id"]] = rec["triples"]
#         except Exception:
#             continue
#     if done:
#         log.info("Resume: loaded %d previously-extracted docs from checkpoint", len(done))
#     return done


# def _append_checkpoint(doc_id: str, triples: List[Dict]) -> None:
#     """Append one doc's triples to the JSONL checkpoint (durable per-doc save)."""
#     with open(TRIPLES_CHECKPOINT, "a", encoding="utf-8") as f:
#         f.write(json.dumps({"doc_id": doc_id, "triples": triples}) + "\n")


# def build_sage_kg(papers: List[Dict], resume: bool = True):
#     """Extract typed triples -> per-doc local graphs -> fuse -> GraphML.

#     Triples are checkpointed to disk per-document (JSONL); a re-run skips docs
#     already extracted, so a crash never loses completed LLM work.
#     """
#     import sage_kg

#     log.info("=" * 60)
#     log.info("STAGE B+C: Triple extraction + per-document local graphs")
#     log.info("=" * 60)
#     generate_structured, cc = _structured_generator()

#     checkpoint = _load_checkpoint() if resume else {}
#     local_graphs, total_triples, start = [], 0, time.time()
#     reused = 0

#     for i, p in enumerate(papers, 1):
#         text = p.get("full_text") or p.get("abstract", "")
#         if not text:
#             continue
#         doc_id = p.get("pmid") or p.get("doi") or p.get("nctid") or f"doc_{i}"

#         if doc_id in checkpoint:
#             triples = checkpoint[doc_id]
#             reused += 1
#         else:
#             try:
#                 triples = sage_kg.extract_triples(text, generate_structured, conf_threshold=0.5)
#             except Exception:
#                 continue
#             _append_checkpoint(doc_id, triples)  # durable save before anything else

#         if not triples:
#             continue
#         local_graphs.append(sage_kg.build_local_graph(triples, doc_id))
#         total_triples += len(triples)
#         if i % 10 == 0:
#             log.info("  [%d/%d] %d local graphs, %d triples (reused %d)",
#                      i, len(papers), len(local_graphs), total_triples, reused)

#     log.info("Extraction: %d local graphs, %d triples (c>=0.5), %d reused, in %.1f min",
#              len(local_graphs), total_triples, reused, (time.time() - start) / 60)
#     log.info("  LLM calls: %d (%d errors)", cc["count"], cc["errors"])

#     G = sage_kg.fuse_graphs(local_graphs, cache_dir=EMBED_CACHE_DIR, tau=0.9, min_component=3)
#     graphml = sage_kg.save_graph(G, OUTPUT_DIR, graph_root=GRAPH_ROOT)
#     return graphml, G


# # =============================================================================
# # Main
# # =============================================================================

# def save_error_report():
#     report = {"timestamp": datetime.now().isoformat(),
#               "total_errors": sum(len(v) for v in errors.values()), "errors": errors}
#     (LOG_DIR / "error_report.json").write_text(json.dumps(report, indent=2))


# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--queries", default="keywords/expanded_search_queries.json")
#     ap.add_argument("--max-per-query", type=int, default=25)
#     ap.add_argument("--drop-frac", type=float, default=0.40)
#     ap.add_argument("--no-s2", action="store_true", help="disable Semantic Scholar search")
#     ap.add_argument("--limit-papers", type=int, default=0, help="cap papers after fetch (0=all)")
#     ap.add_argument("--no-openalex", action="store_true", help="skip OpenAlex citation/venue enrichment")
#     ap.add_argument("--fresh", action="store_true",
#                     help="ignore + overwrite triples checkpoint (re-extract everything)")
#     ap.add_argument("--from-checkpoint", action="store_true",
#                     help="rebuild graph from cached triples only — skip fetch/full-text/extract")
#     ap.add_argument("--comp-queries", default="keywords/comp_search_queries.json",
#                     help="path to computational/ML queries JSON")
#     ap.add_argument("--comp-frac", type=float, default=0.15,
#                     help="target fraction of corpus from computational queries (0 = disable)")
#     ap.add_argument("--manual-docs", default="inputs/sage_kg/manual_docs.json",
#                     help="path to JSON file of manually curated docs (textbooks, etc.) injected before Stage A")
#     ap.add_argument("--ct-queries", default="keywords/clinicaltrials_queries.json",
#                     help="path to ClinicalTrials.gov query JSON (same schema as --queries)")
#     ap.add_argument("--ct-max-per-query", type=int, default=25,
#                     help="max trials fetched per ClinicalTrials.gov query")
#     ap.add_argument("--no-clinicaltrials", action="store_true",
#                     help="disable ClinicalTrials.gov search even if --ct-queries exists")
#     ap.add_argument("--source-weights", default="",
#                     help='JSON string mapping source name -> relative weight, e.g. '
#                          '\'{"clinicaltrials": 2.0}\'. Sources: pubmed, semantic_scholar, '
#                          'clinicaltrials, manual. Weight 1.0 = default filtering; higher '
#                          'keeps relatively more of that source, lower keeps relatively less.')
#     ap.add_argument("--base-graph", default="",
#                     help="path to an existing .graphml to merge new content into, instead "
#                          "of building a fresh graph from scratch (enhance mode)")
#     ap.add_argument("--nct-id", default="",
#                     help="fetch exactly one ClinicalTrials.gov trial by NCT ID and merge it "
#                          "into --base-graph (enhance mode; skips the normal search pipeline)")
#     args = ap.parse_args()

#     if args.fresh and TRIPLES_CHECKPOINT.exists():
#         TRIPLES_CHECKPOINT.unlink()
#         log.info("--fresh: cleared triples checkpoint")

#     import sage_kg
#     t0 = time.time()
#     log.info("=" * 60)
#     log.info("SAGE Knowledge Graph Builder — started %s",
#              datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
#     log.info("=" * 60)

#     # Fast path: enhance mode — merge one specific trial into an existing graph.
#     # Used by the UI's "Add Source" box when a clinicaltrials.gov URL is pasted.
#     if args.nct_id:
#         if not args.base_graph:
#             raise SystemExit("--nct-id requires --base-graph (which graph to merge into)")
#         trial = clinicaltrials_fetch_by_id(args.nct_id)
#         if not trial:
#             log.error("Could not fetch or parse trial %s", args.nct_id)
#             save_error_report()
#             raise SystemExit(f"Failed to fetch {args.nct_id}")
#         graphml, G = enhance_existing_graph(Path(args.base_graph), [trial])
#         log.info("=" * 60)
#         log.info("COMPLETE (enhance: single trial) in %.1f min", (time.time() - t0) / 60)
#         log.info("  GraphML: %s", graphml)
#         log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
#         log.info("=" * 60)
#         save_error_report()
#         return

#     # Fast path: rebuild the graph straight from cached triples (no network/LLM).
#     if args.from_checkpoint:
#         graphml, G = rebuild_from_checkpoint()
#         log.info("=" * 60)
#         log.info("COMPLETE (from checkpoint) in %.1f min", (time.time() - t0) / 60)
#         log.info("  GraphML: %s", graphml)
#         log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
#         log.info("=" * 60)
#         return

#     try:
#         # Phase 2: search (title + abstract + identifiers)
#         papers = fetch_all_papers(args.queries, args.max_per_query, use_s2=not args.no_s2,
#                                   comp_queries_file=args.comp_queries, comp_frac=args.comp_frac)
#         if args.limit_papers:
#             papers = papers[:args.limit_papers]
#             log.info("Capped to %d papers (testing)", len(papers))

#         # Phase 2c: ClinicalTrials.gov — additive, on top of the PubMed/S2 search
#         # above. Does not replace or narrow it; results are merged in before
#         # Stage A filtering, same as manual docs below.
#         if not args.no_clinicaltrials:
#             ct_trials = fetch_all_clinicaltrials(args.ct_queries, args.ct_max_per_query)
#             if ct_trials:
#                 papers.extend(ct_trials)
#                 papers = deduplicate(papers)

#         # Manual docs (textbooks, etc.) — injected with full_text pre-set, bypass Phase 3
#         manual = load_manual_docs(args.manual_docs)
#         if manual:
#             papers.extend(manual)
#             papers = deduplicate(papers)

#         # Enrich with OpenAlex citations + venue (needed by the relevance filter).
#         if not args.no_openalex:
#             papers = enrich_openalex(papers)

#         # Stage A: relevance filter BEFORE full-text retrieval, so we only
#         # download the full text of papers we actually keep (no wasted PMC /
#         # Unpaywall fetches on low-relevance / low-venue papers).
#         with open(args.queries) as f:
#             queries = json.load(f)
#         source_weights = json.loads(args.source_weights) if args.source_weights else None
#         papers = filter_papers_weighted(papers, queries, cache_dir=EMBED_CACHE_DIR,
#                                         drop_frac=args.drop_frac, source_weights=source_weights)
#         if not papers:
#             log.error("No papers survived filtering.")
#             save_error_report()
#             return

#         # Phase 3: full-text retrieval (only for the kept papers)
#         papers = attach_fulltext(papers, ncbi_key=NCBI_API_KEY)

#         (CORPUS_DIR / "paper_metadata.json").write_text(json.dumps(papers, indent=2))
#         log.info("Metadata saved: %d kept papers (full text retrieved)", len(papers))

#         graphml, G = build_sage_kg(papers, resume=not args.fresh)

#         log.info("=" * 60)
#         log.info("COMPLETE in %.1f min", (time.time() - t0) / 60)
#         log.info("  GraphML: %s", graphml)
#         log.info("  Nodes: %d  Edges: %d", G.number_of_nodes(), G.number_of_edges())
#         log.info("=" * 60)
#     except Exception as e:
#         log.error("Fatal: %s\n%s", e, traceback.format_exc())
#         save_error_report()
#         raise
#     save_error_report()


# if __name__ == "__main__":
#     main()
