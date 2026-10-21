"""
Ingest a single document (PDF, .txt, .xml) or URL into an existing GraphML KG.

Extracts typed triples via the SAGE pipeline (GPT-4o-mini) and merges them
into the given graph, then writes a new timestamped GraphML file.

Usage:
    python ingest_source.py --graph outputs/sage_kg/my_kg.graphml \
                            --file /tmp/paper.pdf \
                            --out-dir outputs/sage_kg

    python ingest_source.py --graph outputs/sage_kg/my_kg.graphml \
                            --url https://pubmed.ncbi.nlm.nih.gov/12345678/ \
                            --out-dir outputs/sage_kg
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import networkx as nx
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ingest_source")

# ---------------------------------------------------------------------------
# Env / keys
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
for _env in (ROOT / ".env", Path(".env")):
    if _env.exists():
        for _line in _env.read_text().splitlines():
            if "=" in _line and not _line.startswith("#"):
                k, v = _line.strip().split("=", 1)
                os.environ.setdefault(k, v)
        break

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
EMBED_CACHE_DIR = ROOT / "inputs" / "sage_kg" / "embed_cache"
EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            t = "\n".join(p.extract_text() or "" for p in pdf.pages)
        if len(t.strip()) > 300:
            return t
    except Exception:
        pass
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        t = "\n".join(pg.get_text() for pg in doc)
        if len(t.strip()) > 300:
            return t
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        t = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(pdf_bytes)).pages)
        if len(t.strip()) > 300:
            return t
    except Exception:
        pass
    return ""


def _extract_xml_text(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
        return " ".join(t.strip() for t in root.itertext() if t and t.strip())
    except Exception:
        return xml_bytes.decode("utf-8", errors="replace")


def extract_text_from_file(path: Path) -> str:
    suf = path.suffix.lower()
    data = path.read_bytes()
    if suf == ".pdf":
        return _extract_pdf_text(data)
    if suf == ".xml":
        return _extract_xml_text(data)
    return data.decode("utf-8", errors="replace")


def extract_text_from_url(url: str) -> str:
    """Fetch URL and extract text. Handles PubMed, DOI, and plain HTML/PDF."""
    log.info("Fetching URL: %s", url)

    # PubMed page → extract PMID and fetch abstract
    pmid_match = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", url)
    if pmid_match:
        pmid = pmid_match.group(1)
        return _fetch_pubmed_abstract(pmid)

    # DOI resolver
    doi_match = re.search(r"doi\.org/(.+)", url)
    if doi_match:
        doi = doi_match.group(1)
        return _fetch_doi_text(doi)

    # Generic fetch
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "pdf" in ct or r.content[:4] == b"%PDF":
            return _extract_pdf_text(r.content)
        if "xml" in ct:
            return _extract_xml_text(r.content)
        # Strip HTML tags for plain text
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception as e:
        log.error("Failed to fetch URL: %s", e)
        return ""


def _fetch_pubmed_abstract(pmid: str) -> str:
    try:
        r = requests.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
            params={"db": "pubmed", "id": pmid, "rettype": "abstract", "retmode": "text"},
            timeout=15,
        )
        return r.text if r.ok else ""
    except Exception as e:
        log.warning("PubMed fetch failed for %s: %s", pmid, e)
        return ""


def _fetch_doi_text(doi: str) -> str:
    try:
        r = requests.get(f"https://doi.org/{doi}", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"})
        if r.ok:
            if r.content[:4] == b"%PDF":
                return _extract_pdf_text(r.content)
            text = re.sub(r"<[^>]+>", " ", r.text)
            return re.sub(r"\s+", " ", text).strip()
    except Exception as e:
        log.warning("DOI fetch failed for %s: %s", doi, e)
    return ""


# ---------------------------------------------------------------------------
# LLM structured output (mirrors build_kg.py)
# ---------------------------------------------------------------------------

def _make_generator():
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Triple extraction requires an OpenAI key."
        )
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    cc = {"count": 0, "errors": 0}

    def generate_structured(system_prompt: str, user_prompt: str, schema: dict):
        cc["count"] += 1
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
            temperature=0.1,
            max_tokens=4096,
            response_format=schema,
        )
        return json.loads(resp.choices[0].message.content)

    return generate_structured, cc


# ---------------------------------------------------------------------------
# Graph merge (simple node/edge union — avoids expensive BGE re-fusion)
# ---------------------------------------------------------------------------

def merge_local_into_global(G_global: nx.DiGraph, G_local: nx.DiGraph) -> int:
    """Add nodes/edges from G_local into G_global. Returns count of new edges."""
    added = 0
    for n, d in G_local.nodes(data=True):
        if not G_global.has_node(n):
            G_global.add_node(n, **d)
    for u, v, d in G_local.edges(data=True):
        if G_global.has_edge(u, v):
            # Accumulate weight
            G_global[u][v]["weight"] = G_global[u][v].get("weight", 0) + d.get("weight", 0)
        else:
            G_global.add_edge(u, v, **d)
            added += 1
    return added


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Ingest a single source into an existing GraphML KG.")
    ap.add_argument("--graph", required=True, help="Path to existing .graphml file")
    ap.add_argument("--file", default="", help="Path to PDF, .txt, or .xml to ingest")
    ap.add_argument("--url", default="", help="URL to fetch and ingest")
    ap.add_argument("--out-dir", default="", help="Directory for the updated graph (default: same as --graph)")
    ap.add_argument("--graph-root", default="", help="Base name for output file (default: derived from input)")
    args = ap.parse_args()

    graph_path = Path(args.graph)
    if not graph_path.exists():
        log.error("Graph not found: %s", graph_path)
        sys.exit(1)

    if not args.file and not args.url:
        log.error("Provide --file or --url")
        sys.exit(1)

    # --- Extract text ---
    if args.file:
        src_path = Path(args.file)
        log.info("Extracting text from file: %s", src_path)
        text = extract_text_from_file(src_path)
        doc_id = src_path.stem
    else:
        text = extract_text_from_url(args.url)
        doc_id = re.sub(r"[^a-zA-Z0-9_-]", "_", args.url)[:60]

    if not text or len(text.strip()) < 100:
        log.error("Could not extract sufficient text (got %d chars). Aborting.", len(text))
        sys.exit(1)
    log.info("Extracted %d chars of text (doc_id=%s)", len(text), doc_id)

    # --- Load existing graph ---
    log.info("Loading existing graph: %s", graph_path)
    G_global = nx.read_graphml(str(graph_path))
    log.info("  Existing: %d nodes, %d edges", G_global.number_of_nodes(), G_global.number_of_edges())

    # --- Triple extraction ---
    import sage_kg
    log.info("Extracting triples (SAGE pipeline)…")
    try:
        generate_structured, cc = _make_generator()
        triples = sage_kg.extract_triples(text, generate_structured, conf_threshold=0.5)
    except Exception as e:
        log.error("Triple extraction failed: %s", e)
        sys.exit(1)
    log.info("  Extracted %d triples (c>=0.5), LLM calls: %d", len(triples), cc["count"])

    if not triples:
        log.warning("No triples extracted — graph unchanged.")
        sys.exit(0)

    # --- Build local graph and merge ---
    G_local = sage_kg.build_local_graph(triples, doc_id)
    log.info("  Local graph: %d nodes, %d edges", G_local.number_of_nodes(), G_local.number_of_edges())

    n_added = merge_local_into_global(G_global, G_local)
    log.info("  After merge: %d nodes, %d edges (+%d new edges)",
             G_global.number_of_nodes(), G_global.number_of_edges(), n_added)

    # --- Save ---
    out_dir = Path(args.out_dir) if args.out_dir else graph_path.parent
    graph_root = args.graph_root or graph_path.stem.rsplit("_", 2)[0]
    out_path = sage_kg.save_graph(G_global, out_dir, graph_root=graph_root)
    log.info("Saved: %s", out_path)
    print(f"OUTPUT_GRAPHML={out_path}")


if __name__ == "__main__":
    main()
