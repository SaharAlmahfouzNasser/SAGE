"""
SAGE Hypothesis Generation Engine — FastAPI backend
Run from the project root:
    pip install fastapi uvicorn
    uvicorn UI.server:app --reload --port 8000
Or directly:
    python UI/server.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import List, Optional

import networkx as nx
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent.parent          # project root
UI_DIR    = Path(__file__).parent                 # UI/
GRAPHS_DIR = ROOT / "outputs" / "sage_kg"
OUTPUTS_DIR = ROOT / "outputs"
DEFAULT_GRAPHS_DIR = UI_DIR / "default_graphs"
INITIAL_GRAPH_DIR = ROOT / "inputs" / "initial_graph_biomed"
PATH_DISCOVERY_DIR = ROOT / "path_discovery"
QUERIES_UPLOAD_DIR = ROOT / "keywords" / "uploads"

NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)

BUILTIN_GRAPHS = {
    "builtin-bladder-cancer": {
        "label": "Bladder Cancer (built-in)",
        "domain": "bladder cancer",
        "candidates": [
            DEFAULT_GRAPHS_DIR / "bladder_cancer_kg_latest.graphml",
            DEFAULT_GRAPHS_DIR / "bladder_cancer_kg.graphml",
            INITIAL_GRAPH_DIR / "merged_KG.graphml",
        ],
    },
    "builtin-breast-cancer": {
        "label": "Breast Cancer (built-in)",
        "domain": "breast cancer",
        "candidates": [
            DEFAULT_GRAPHS_DIR / "breast_cancer_kg_latest.graphml",
            DEFAULT_GRAPHS_DIR / "breast_cancer_kg.graphml",
            INITIAL_GRAPH_DIR / "BreastCancer_separated.graphml",
        ],
    },
    "builtin-prostate-cancer": {
        "label": "Prostate Cancer (built-in)",
        "domain": "prostate cancer",
        "candidates": [
            DEFAULT_GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
            DEFAULT_GRAPHS_DIR / "prostate_cancer_kg.graphml",
            GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
            GRAPHS_DIR / "prostate_cancer_kg.graphml",
        ],
    },
}

BUILTIN_GRAPHS_FILE = UI_DIR / "builtin_graphs.json"


def _load_custom_builtins() -> None:
    """Merge any user-saved 'built-in' graphs (from the Save as built-in
    action) into BUILTIN_GRAPHS. Runs once at startup so custom built-ins
    survive a server restart — the hardcoded three above are never touched
    or overwritten by this."""
    if not BUILTIN_GRAPHS_FILE.exists():
        return
    try:
        saved = json.loads(BUILTIN_GRAPHS_FILE.read_text(encoding="utf-8"))
        for key, spec in saved.items():
            BUILTIN_GRAPHS[key] = {
                "label": spec["label"],
                "domain": spec["domain"],
                "candidates": [Path(p) for p in spec["candidates"]],
            }
    except Exception as e:
        print(f"[builtin_graphs] failed to load {BUILTIN_GRAPHS_FILE}: {e}")


def _persist_custom_builtins() -> None:
    """Write every BUILTIN_GRAPHS entry that isn't one of the three original
    hardcoded ones out to disk, so custom built-ins survive a restart."""
    original_keys = {"builtin-bladder-cancer", "builtin-breast-cancer", "builtin-prostate-cancer"}
    custom = {
        key: {
            "label": spec["label"],
            "domain": spec["domain"],
            "candidates": [str(p) for p in spec["candidates"]],
        }
        for key, spec in BUILTIN_GRAPHS.items()
        if key not in original_keys
    }
    BUILTIN_GRAPHS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUILTIN_GRAPHS_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")


_load_custom_builtins()

app = FastAPI(title="SAGE Hypothesis Generation Engine", version="1.0")

# ── In-memory job store ────────────────────────────────────────────────────────
JOBS: dict[str, dict] = {}


# ── Pydantic models ────────────────────────────────────────────────────────────

class BuildKGRequest(BaseModel):
    domain: str = "prostate cancer"
    keywords: List[str] = []
    max_papers: int = 50
    conf_threshold: float = 0.5
    use_s2: bool = False
    use_clinicaltrials: bool = True
    source_weights: Optional[dict[str, float]] = None
    query_file: Optional[str] = None  # filename returned by /api/queries/upload

class HypothesisRequest(BaseModel):
    graph_filename: str
    query: str

class PathDiscoveryRequest(BaseModel):
    graph_filename: str
    query: str = "Find underexplored prognostic biomarkers in prostate cancer."
    source: str = ""
    target_type: str = ""
    domain: str = "prostate cancer"
    max_targets: int = 30
    top_n: int = 10
    seed: int = 7
    logic_weight: float = 0.2
    relevance_weight: float = 0.2
    novelty_weight: float = 0.3
    surprise_weight: float = 0.3
    rank_by: str = "total"
    skip_logic: bool = False

class EnhanceRequest(BaseModel):
    graph_filename: str
    enhance_type: str          # "url" | "keywords"
    url: Optional[str] = None
    keywords: Optional[List[str]] = None
    source_types: Optional[List[str]] = None
    source_type: Optional[str] = None  # single label, used for the "Add Source" tab
    max_papers: int = 20
    max_papers_per_source: Optional[dict[str, int]] = None  # used for "Add from Keywords"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _json_paths(filename: str) -> tuple[Path, Path]:
    """Return the nodes/edges JSON paths for a given .graphml filename."""
    stem = filename.replace(".graphml", "")
    base = GRAPHS_DIR / stem
    return Path(str(base) + "_nodes.json"), Path(str(base) + "_edges.json")


def _find_builtin_graph(graph_id: str) -> tuple[dict | None, Path | None]:
    spec = BUILTIN_GRAPHS.get(graph_id)
    if not spec:
        return None, None

    for candidate in spec["candidates"]:
        if candidate.exists():
            return spec, candidate
    return spec, None


def _resolve_graph_path(graph_ref: str) -> tuple[Path, dict]:
    """Resolve either a built-in graph ID or a user-uploaded graph filename."""
    spec, builtin_path = _find_builtin_graph(graph_ref)
    if spec is not None:
        if builtin_path is None:
            raise HTTPException(404, f"Built-in graph is not available: {graph_ref}")
        return builtin_path, {
            "filename": graph_ref,
            "label": spec["label"],
            "domain": spec["domain"],
            "is_builtin": True,
        }

    graphml_path = GRAPHS_DIR / graph_ref
    if graphml_path.exists():
        return graphml_path, {
            "filename": graph_ref,
            "label": graph_ref,
            "domain": None,
            "is_builtin": False,
        }

    raise HTTPException(404, f"Graph not found: {graph_ref}")


def _load_graph_from_graphml(graphml_path: Path) -> tuple[list, list]:
    """Parse a .graphml file directly with NetworkX and return nodes/edges lists."""
    G = nx.read_graphml(str(graphml_path))
    nodes = [
        {"id": str(n), "label": str(d.get("label", n)), "type": str(d.get("type", "Other"))}
        for n, d in G.nodes(data=True)
    ]
    edges = [
        {"source": str(u), "target": str(v), "weight": float(d.get("weight", 0.7)),
         "title": str(d.get("title", "")), "rnorm": str(d.get("rnorm", "related_to"))}
        for u, v, d in G.edges(data=True)
    ]
    return nodes, edges


def _load_graph_data(filename: str) -> tuple[list, list]:
    """Load nodes and edges — prefer pre-built JSON, fall back to parsing the graphml."""
    nodes_p, edges_p = _json_paths(filename)
    if nodes_p.exists():
        nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
        edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
        return nodes, edges

    graphml_path = GRAPHS_DIR / filename
    if graphml_path.exists():
        return _load_graph_from_graphml(graphml_path)

    return [], []


def _load_graph_data(graph_ref: str) -> tuple[list, list]:
    """Load nodes and edges, preferring cached JSON sidecars when available."""
    graphml_path, meta = _resolve_graph_path(graph_ref)

    if not meta["is_builtin"]:
        nodes_p, edges_p = _json_paths(graph_ref)
        if nodes_p.exists():
            nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
            edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
            return nodes, edges

    return _load_graph_from_graphml(graphml_path)


def _make_job(initial_progress: str = "Starting…") -> str:
    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {
        "status": "running",
        "progress": initial_progress,
        "result": None,
        "error": None,
        "started_at": time.time(),
        "duration_seconds": None,
        "metrics": None,  # populated from a "SAGE_METRICS: {json}" stdout line, if the
                           # subprocess prints one (see build_kg.py / run_pipeline.py)
    }
    return job_id


METRICS_PREFIX = "SAGE_METRICS:"


def _run_subprocess(cmd: list[str], job_id: str, on_done) -> None:
    """Run a subprocess in a daemon thread, streaming stdout to job progress.

    Any line the subprocess prints starting with "SAGE_METRICS:" followed by
    a JSON object is captured into JOBS[job_id]["metrics"] instead of being
    shown as progress text — this is how build_kg.py / run_pipeline.py report
    token usage, LLM call counts, etc. back to the UI. Example line a script
    can print: print(f"SAGE_METRICS: {json.dumps({'total_tokens': 4213, 'llm_calls': 12})}")
    """
    def _worker():
        try:
            env = {**os.environ}
            # ensure .env is sourced (run_pipeline and build_kg do their own load_env)
            proc = subprocess.Popen(
                cmd, cwd=str(ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            stderr_lines: list[str] = []

            def handle_stdout_line(line: str) -> None:
                if line.startswith(METRICS_PREFIX):
                    try:
                        JOBS[job_id]["metrics"] = json.loads(line[len(METRICS_PREFIX):].strip())
                    except Exception:
                        pass  # malformed metrics line — don't let it break the run
                else:
                    JOBS[job_id]["progress"] = line[:160]

            import selectors
            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)
            sel.register(proc.stderr, selectors.EVENT_READ)
            while True:
                events = sel.select(timeout=1)
                for key, _ in events:
                    line = key.fileobj.readline()
                    if not line:
                        continue
                    line = line.rstrip()
                    if key.fileobj is proc.stdout:
                        if line:
                            handle_stdout_line(line)
                    else:
                        if line:
                            stderr_lines.append(line)
                            JOBS[job_id]["progress"] = f"[err] {line[:150]}"
                if proc.poll() is not None:
                    # drain remaining output
                    for line in proc.stdout:
                        line = line.rstrip()
                        if line:
                            handle_stdout_line(line)
                    for line in proc.stderr:
                        line = line.rstrip()
                        if line:
                            stderr_lines.append(line)
                    break
            sel.close()

            JOBS[job_id]["duration_seconds"] = round(time.time() - JOBS[job_id]["started_at"], 1)

            if proc.returncode == 0:
                on_done(job_id)
            else:
                err_detail = "\n".join(stderr_lines[-20:]) if stderr_lines else ""
                JOBS[job_id]["status"] = "failed"
                JOBS[job_id]["error"] = (
                    f"Process exited with code {proc.returncode}"
                    + (f"\n{err_detail}" if err_detail else "")
                )
        except Exception as exc:
            JOBS[job_id]["duration_seconds"] = round(time.time() - JOBS[job_id]["started_at"], 1)
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(exc)

    threading.Thread(target=_worker, daemon=True).start()


# ── Static / root ──────────────────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    return FileResponse(UI_DIR / "index.html")


@app.get("/graph")
async def serve_graph():
    return FileResponse(UI_DIR / "graph.html")


# ── Graph list ─────────────────────────────────────────────────────────────────

@app.get("/api/graphs")
async def list_graphs():
    """List all .graphml files with basic stats."""
    result = []

    for graph_id, spec in BUILTIN_GRAPHS.items():
        _, builtin_path = _find_builtin_graph(graph_id)
        if builtin_path is None:
            continue
        nodes, edges = _load_graph_data(graph_id)
        result.append({
            "filename": graph_id,
            "label": spec["label"],
            "domain": spec["domain"],
            "modified": builtin_path.stat().st_mtime,
            "nodes": len(nodes),
            "edges": len(edges),
            "is_builtin": True,
        })

    files = []
    if GRAPHS_DIR.exists():
        files = sorted(
            GRAPHS_DIR.glob("*.graphml"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

    for f in files:
        nodes, edges = _load_graph_data(f.name)
        result.append({
            "filename": f.name,
            "label": f.name,
            "domain": None,
            "modified": f.stat().st_mtime,
            "nodes": len(nodes),
            "edges": len(edges),
            "is_builtin": False,
        })

    result.sort(key=lambda g: g["modified"], reverse=True)
    return {"graphs": result}


# ── Graph data (Cytoscape elements) ───────────────────────────────────────────

@app.get("/api/graphs/{filename}")
async def get_graph(filename: str, limit: int = 400):
    """
    Return graph stats + Cytoscape elements.
    Only the top `limit` nodes by degree are returned to keep rendering fast.
    """
    nodes, edges = _load_graph_data(filename)
    if not nodes:
        raise HTTPException(404, f"No data found for graph: {filename}")
    _, graph_meta = _resolve_graph_path(filename)

    # Degree from edges
    degree: dict[str, int] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s:
            degree[s] = degree.get(s, 0) + 1
        if t:
            degree[t] = degree.get(t, 0) + 1

    # Top-N nodes by degree
    top_nodes = sorted(nodes, key=lambda n: degree.get(n["id"], 0), reverse=True)[:limit]
    top_ids = {n["id"] for n in top_nodes}

    # Edges whose both endpoints are in the shown set
    top_edges = [
        e for e in edges
        if e.get("source") in top_ids and e.get("target") in top_ids
    ]

    # Full-graph type counts
    type_counts = dict(Counter(n.get("type", "Other") for n in nodes))

    cy_nodes = [
        {
            "data": {
                "id": n["id"],
                "label": n.get("label", n["id"])[:50],
                "type": n.get("type", "Other"),
                "degree": degree.get(n["id"], 0),
            }
        }
        for n in top_nodes
    ]
    cy_edges = [
        {
            "data": {
                "source": e["source"],
                "target": e["target"],
                "label": e.get("rnorm", e.get("title", "related_to")),
                "conf": float(e.get("weight", 0.7)),
            }
        }
        for e in top_edges
    ]

    return {
        "graph": graph_meta,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "shown_nodes": len(top_nodes),
            "shown_edges": len(top_edges),
            "node_types": type_counts,
        },
        "elements": cy_nodes + cy_edges,
    }


# ── Download graphml ───────────────────────────────────────────────────────────

@app.get("/api/graphs/{filename}/download")
async def download_graph(filename: str):
    """Serve the raw .graphml file so the browser can download it directly."""
    graphml_path, meta = _resolve_graph_path(filename)
    download_name = f"{meta['label'].replace(' ', '_')}.graphml" if meta["is_builtin"] else filename
    return FileResponse(
        path=str(graphml_path),
        filename=download_name,
        media_type="application/xml",
    )


# ── Upload graphml ────────────────────────────────────────────────────────────

@app.post("/api/graphs/upload")
async def upload_graph(file: UploadFile = File(...)):
    if not file.filename.endswith(".graphml"):
        raise HTTPException(400, "Only .graphml files are accepted.")
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    dest = GRAPHS_DIR / file.filename
    dest.write_bytes(await file.read())
    return {"filename": file.filename}


class SaveAsBuiltinRequest(BaseModel):
    label: str
    domain: str


@app.post("/api/graphs/{filename}/save-as-builtin")
async def save_as_builtin(filename: str, req: SaveAsBuiltinRequest):
    """Copy a non-builtin graph into UI/default_graphs/ and register it as a
    built-in, persisted to disk so it survives a server restart. Refuses if
    the filename is already a built-in ID (nothing to copy/promote)."""
    if filename in BUILTIN_GRAPHS:
        raise HTTPException(400, f"'{filename}' is already a built-in graph.")

    src = GRAPHS_DIR / filename
    if not src.exists():
        raise HTTPException(404, f"Graph not found: {filename}")

    slug = "".join(c if c.isalnum() else "_" for c in req.domain.lower()).strip("_") or "custom"
    key = f"builtin-{slug}"
    suffix = 2
    base_key = key
    while key in BUILTIN_GRAPHS:
        key = f"{base_key}-{suffix}"
        suffix += 1

    DEFAULT_GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    dest = DEFAULT_GRAPHS_DIR / f"{slug}_kg_latest.graphml"
    dest.write_bytes(src.read_bytes())

    BUILTIN_GRAPHS[key] = {
        "label": req.label,
        "domain": req.domain,
        "candidates": [dest],
    }
    _persist_custom_builtins()

    return {"key": key, "label": req.label, "domain": req.domain}


# ── Build KG ───────────────────────────────────────────────────────────────────

@app.post("/api/graphs/build")
async def build_graph(req: BuildKGRequest):
    """Start a build_kg.py run in the background and return a job ID."""
    job_id = _make_job("Preparing build…")

    cmd = [
        sys.executable, str(ROOT / "build_kg.py"),
        "--max-per-query", str(req.max_papers),
        "--domain", req.domain,
    ]
    if not req.use_s2:
        cmd.append("--no-s2")
    if not req.use_clinicaltrials:
        cmd.append("--no-clinicaltrials")
    if req.source_weights:
        cmd += ["--source-weights", json.dumps(req.source_weights)]
    if req.query_file:
        query_path = QUERIES_UPLOAD_DIR / req.query_file
        if query_path.exists():
            cmd += ["--queries", str(query_path)]
        else:
            raise HTTPException(404, f"Uploaded query file not found: {req.query_file}")

    def on_done(jid):
        files = sorted(
            GRAPHS_DIR.glob("*.graphml"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = {"filename": files[0].name if files else None}

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


# ── Upload a search-query file (used by the Create Graph form) ────────────────

@app.post("/api/queries/upload")
async def upload_queries(file: UploadFile = File(...)):
    if not (file.filename.endswith(".json") or file.filename.endswith(".txt")):
        raise HTTPException(400, "Only .json or .txt query files are accepted.")
    QUERIES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = QUERIES_UPLOAD_DIR / file.filename
    content = await file.read()
    if file.filename.endswith(".json"):
        try:
            json.loads(content)
        except Exception:
            raise HTTPException(400, "That .json file isn't valid JSON.")
    dest.write_bytes(content)
    return {"filename": file.filename}


# ── Enhance KG ─────────────────────────────────────────────────────────────────

@app.post("/api/graphs/enhance")
async def enhance_graph(req: EnhanceRequest):
    """
    Enhance an existing graph with new sources.
    Currently supports keyword-based expansion (re-runs build_kg with new queries).
    URL/file ingestion is a planned feature.
    """
    if req.enhance_type == "url":
        if not req.url:
            raise HTTPException(400, "Provide a url.")

        graph_path, _ = _resolve_graph_path(req.graph_filename)
        nct_match = NCT_ID_RE.search(req.url)

        if nct_match:
            job_id = _make_job(f"Fetching trial {nct_match.group(0)}…")
            cmd = [
                sys.executable, str(ROOT / "build_kg.py"),
                "--nct-id", nct_match.group(0),
                "--base-graph", str(graph_path),
            ]
        else:
            # Generic URL — best-effort text extraction, works for most
            # readable pages (abstracts, news, etc.) but not paywalled or
            # JS-rendered content.
            job_id = _make_job(f"Fetching {req.url}…")
            cmd = [
                sys.executable, str(ROOT / "build_kg.py"),
                "--generic-url", req.url,
                "--base-graph", str(graph_path),
            ]
            if req.source_type:
                cmd += ["--source-type", req.source_type]

        def on_done_url(jid):
            files = sorted(
                GRAPHS_DIR.glob("*.graphml"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            JOBS[jid]["status"] = "done"
            JOBS[jid]["result"] = {"filename": files[0].name if files else None}

        _run_subprocess(cmd, job_id, on_done_url)
        return {"job_id": job_id}

    if req.enhance_type != "keywords" or not req.keywords:
        raise HTTPException(400, "Provide enhance_type='keywords' and a non-empty keywords list.")

    graph_path, _ = _resolve_graph_path(req.graph_filename)
    job_id = _make_job("Running targeted literature search…")

    per_source_max = req.max_papers_per_source or {"pubmed": req.max_papers}

    cmd = [
        sys.executable, str(ROOT / "build_kg.py"),
        "--base-graph", str(graph_path),
        "--enhance-keywords", json.dumps(req.keywords),
        "--per-source-max", json.dumps(per_source_max),
    ]

    def on_done(jid):
        files = sorted(
            GRAPHS_DIR.glob("*.graphml"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = {"filename": files[0].name if files else None}

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


# ── Enhance with an uploaded file (PDF/.txt/.xml) ──────────────────────────────

ENHANCE_UPLOAD_DIR = ROOT / "inputs" / "sage_kg" / "enhance_uploads"


@app.post("/api/graphs/{filename}/enhance-file")
async def enhance_with_file(filename: str, file: UploadFile = File(...), source_type: str = ""):
    if not file.filename.lower().endswith((".pdf", ".txt", ".xml")):
        raise HTTPException(400, "Only .pdf, .txt, or .xml files are accepted.")

    graph_path, _ = _resolve_graph_path(filename)
    ENHANCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = ENHANCE_UPLOAD_DIR / file.filename
    dest.write_bytes(await file.read())

    job_id = _make_job(f"Ingesting {file.filename}…")
    cmd = [
        sys.executable, str(ROOT / "build_kg.py"),
        "--local-file", str(dest),
        "--base-graph", str(graph_path),
    ]
    if source_type:
        cmd += ["--source-type", source_type]

    def on_done(jid):
        files = sorted(
            GRAPHS_DIR.glob("*.graphml"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = {"filename": files[0].name if files else None}

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


# ── Hypothesis generation ──────────────────────────────────────────────────────

@app.post("/api/hypothesis")
async def generate_hypothesis(req: HypothesisRequest):
    """Run the full SciAgents hypothesis pipeline and return a job ID."""
    graph_path, _ = _resolve_graph_path(req.graph_filename)

    job_id = _make_job("Initialising agents…")

    cmd = [
        sys.executable, str(ROOT / "run_pipeline.py"),
        "--graph", str(graph_path),
        "--query", req.query,
    ]

    def on_done(jid):
        # Find the most recently written hypothesis .md
        md_files = sorted(
            list(GRAPHS_DIR.glob("hypothesis_run*.md"))
            + list(OUTPUTS_DIR.rglob("hypothesis_run*.md")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        hypothesis_text = md_files[0].read_text(encoding="utf-8") if md_files else ""
        duration = JOBS[jid]["duration_seconds"]
        metrics = JOBS[jid]["metrics"] or {}
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = {
            "hypothesis": hypothesis_text[:8000],
            "duration_seconds": duration,
            "metrics": metrics,
        }
        _log_hypothesis_metrics(req.graph_filename, req.query, duration, metrics)

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


HYPOTHESIS_METRICS_LOG = ROOT / "logs" / "hypothesis_metrics.jsonl"


def _log_hypothesis_metrics(graph_filename: str, query: str, duration_seconds, metrics: dict) -> None:
    """Append one line per hypothesis run to a durable JSONL log, so history
    survives server restarts (JOBS is in-memory only)."""
    try:
        HYPOTHESIS_METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": time.time(),
            "graph_filename": graph_filename,
            "query": query,
            "duration_seconds": duration_seconds,
            **metrics,  # e.g. total_tokens, prompt_tokens, completion_tokens, llm_calls, model
        }
        # derived efficiency metrics, computed where we have enough info
        total_tokens = metrics.get("total_tokens")
        if total_tokens and duration_seconds:
            record["tokens_per_second"] = round(total_tokens / duration_seconds, 1)
        with open(HYPOTHESIS_METRICS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[hypothesis_metrics] failed to log: {e}")


@app.get("/api/hypothesis-metrics")
async def get_hypothesis_metrics(limit: int = 50):
    """Return the most recent hypothesis-generation runs with their timing
    and token metrics, for a history/efficiency view in the UI."""
    if not HYPOTHESIS_METRICS_LOG.exists():
        return {"runs": []}
    lines = HYPOTHESIS_METRICS_LOG.read_text(encoding="utf-8").splitlines()
    runs = []
    for line in lines[-limit:]:
        try:
            runs.append(json.loads(line))
        except Exception:
            continue
    runs.reverse()  # most recent first
    return {"runs": runs}


# ── Focused path discovery ─────────────────────────────────────────────────────

@app.post("/api/path-discovery")
async def run_path_discovery(req: PathDiscoveryRequest):
    """Run only graph-path discovery/ranking and return a job ID."""
    graph_path, _ = _resolve_graph_path(req.graph_filename)

    job_id = _make_job("Preparing path discovery…")
    PATH_DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
    out_txt = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.txt"
    out_json = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.json"

    cmd = [
        sys.executable, str(ROOT / "run_path_discovery.py"),
        "--graph", str(graph_path),
        "--query", req.query,
        "--source", req.source,
        "--target-type", req.target_type,
        "--domain", req.domain,
        "--max-targets", str(req.max_targets),
        "--top-n", str(req.top_n),
        "--seed", str(req.seed),
        "--logic-weight", str(req.logic_weight),
        "--relevance-weight", str(req.relevance_weight),
        "--novelty-weight", str(req.novelty_weight),
        "--surprise-weight", str(req.surprise_weight),
        "--rank-by", req.rank_by,
        "--out", str(out_txt),
        "--json-out", str(out_json),
    ]
    if req.skip_logic:
        cmd.append("--skip-logic")

    def on_done(jid):
        result = {
            "text": out_txt.read_text(encoding="utf-8") if out_txt.exists() else "",
            "text_path": str(out_txt.relative_to(ROOT)) if out_txt.exists() else None,
            "json_path": str(out_json.relative_to(ROOT)) if out_json.exists() else None,
        }
        if out_json.exists():
            try:
                result["data"] = json.loads(out_json.read_text(encoding="utf-8"))
            except Exception:
                result["data"] = None
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = result

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


# ── Job status ─────────────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  SAGE UI  →  http://localhost:8000\n")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(UI_DIR)],
        app_dir=str(UI_DIR),
    )




# """
# SAGE Hypothesis Generation Engine — FastAPI backend
# Run from the project root:
#     pip install fastapi uvicorn
#     uvicorn UI.server:app --reload --port 8000
# Or directly:
#     python UI/server.py
# """

# from __future__ import annotations

# import json
# import os
# import re
# import subprocess
# import sys
# import threading
# import uuid
# from collections import Counter
# from pathlib import Path
# from typing import List, Optional

# import networkx as nx
# import uvicorn
# from fastapi import FastAPI, HTTPException, UploadFile, File
# from fastapi.responses import FileResponse, JSONResponse
# from pydantic import BaseModel

# # ── Paths ─────────────────────────────────────────────────────────────────────
# ROOT      = Path(__file__).parent.parent          # project root
# UI_DIR    = Path(__file__).parent                 # UI/
# GRAPHS_DIR = ROOT / "outputs" / "sage_kg"
# OUTPUTS_DIR = ROOT / "outputs"
# DEFAULT_GRAPHS_DIR = UI_DIR / "default_graphs"
# INITIAL_GRAPH_DIR = ROOT / "inputs" / "initial_graph_biomed"
# PATH_DISCOVERY_DIR = ROOT / "path_discovery"
# QUERIES_UPLOAD_DIR = ROOT / "keywords" / "uploads"

# NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)

# BUILTIN_GRAPHS = {
#     "builtin-bladder-cancer": {
#         "label": "Bladder Cancer (built-in)",
#         "domain": "bladder cancer",
#         "candidates": [
#             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg_latest.graphml",
#             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg.graphml",
#             INITIAL_GRAPH_DIR / "merged_KG.graphml",
#         ],
#     },
#     "builtin-breast-cancer": {
#         "label": "Breast Cancer (built-in)",
#         "domain": "breast cancer",
#         "candidates": [
#             DEFAULT_GRAPHS_DIR / "breast_cancer_kg_latest.graphml",
#             DEFAULT_GRAPHS_DIR / "breast_cancer_kg.graphml",
#             INITIAL_GRAPH_DIR / "BreastCancer_separated.graphml",
#         ],
#     },
#     "builtin-prostate-cancer": {
#         "label": "Prostate Cancer (built-in)",
#         "domain": "prostate cancer",
#         "candidates": [
#             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
#             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg.graphml",
#             GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
#             GRAPHS_DIR / "prostate_cancer_kg.graphml",
#         ],
#     },
# }

# BUILTIN_GRAPHS_FILE = UI_DIR / "builtin_graphs.json"


# def _load_custom_builtins() -> None:
#     """Merge any user-saved 'built-in' graphs (from the Save as built-in
#     action) into BUILTIN_GRAPHS. Runs once at startup so custom built-ins
#     survive a server restart — the hardcoded three above are never touched
#     or overwritten by this."""
#     if not BUILTIN_GRAPHS_FILE.exists():
#         return
#     try:
#         saved = json.loads(BUILTIN_GRAPHS_FILE.read_text(encoding="utf-8"))
#         for key, spec in saved.items():
#             BUILTIN_GRAPHS[key] = {
#                 "label": spec["label"],
#                 "domain": spec["domain"],
#                 "candidates": [Path(p) for p in spec["candidates"]],
#             }
#     except Exception as e:
#         print(f"[builtin_graphs] failed to load {BUILTIN_GRAPHS_FILE}: {e}")


# def _persist_custom_builtins() -> None:
#     """Write every BUILTIN_GRAPHS entry that isn't one of the three original
#     hardcoded ones out to disk, so custom built-ins survive a restart."""
#     original_keys = {"builtin-bladder-cancer", "builtin-breast-cancer", "builtin-prostate-cancer"}
#     custom = {
#         key: {
#             "label": spec["label"],
#             "domain": spec["domain"],
#             "candidates": [str(p) for p in spec["candidates"]],
#         }
#         for key, spec in BUILTIN_GRAPHS.items()
#         if key not in original_keys
#     }
#     BUILTIN_GRAPHS_FILE.parent.mkdir(parents=True, exist_ok=True)
#     BUILTIN_GRAPHS_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")


# _load_custom_builtins()

# app = FastAPI(title="SAGE Hypothesis Generation Engine", version="1.0")

# # ── In-memory job store ────────────────────────────────────────────────────────
# JOBS: dict[str, dict] = {}


# # ── Pydantic models ────────────────────────────────────────────────────────────

# class BuildKGRequest(BaseModel):
#     domain: str = "prostate cancer"
#     keywords: List[str] = []
#     max_papers: int = 50
#     conf_threshold: float = 0.5
#     use_s2: bool = False
#     use_clinicaltrials: bool = True
#     source_weights: Optional[dict[str, float]] = None
#     query_file: Optional[str] = None  # filename returned by /api/queries/upload

# class HypothesisRequest(BaseModel):
#     graph_filename: str
#     query: str

# class PathDiscoveryRequest(BaseModel):
#     graph_filename: str
#     query: str = "Find underexplored prognostic biomarkers in prostate cancer."
#     source: str = ""
#     target_type: str = ""
#     domain: str = "prostate cancer"
#     max_targets: int = 30
#     top_n: int = 10
#     seed: int = 7
#     logic_weight: float = 0.2
#     relevance_weight: float = 0.2
#     novelty_weight: float = 0.3
#     surprise_weight: float = 0.3
#     rank_by: str = "total"
#     skip_logic: bool = False

# class EnhanceRequest(BaseModel):
#     graph_filename: str
#     enhance_type: str          # "url" | "keywords"
#     url: Optional[str] = None
#     keywords: Optional[List[str]] = None
#     source_types: Optional[List[str]] = None
#     max_papers: int = 20


# # ── Helpers ────────────────────────────────────────────────────────────────────

# def _json_paths(filename: str) -> tuple[Path, Path]:
#     """Return the nodes/edges JSON paths for a given .graphml filename."""
#     stem = filename.replace(".graphml", "")
#     base = GRAPHS_DIR / stem
#     return Path(str(base) + "_nodes.json"), Path(str(base) + "_edges.json")


# def _find_builtin_graph(graph_id: str) -> tuple[dict | None, Path | None]:
#     spec = BUILTIN_GRAPHS.get(graph_id)
#     if not spec:
#         return None, None

#     for candidate in spec["candidates"]:
#         if candidate.exists():
#             return spec, candidate
#     return spec, None


# def _resolve_graph_path(graph_ref: str) -> tuple[Path, dict]:
#     """Resolve either a built-in graph ID or a user-uploaded graph filename."""
#     spec, builtin_path = _find_builtin_graph(graph_ref)
#     if spec is not None:
#         if builtin_path is None:
#             raise HTTPException(404, f"Built-in graph is not available: {graph_ref}")
#         return builtin_path, {
#             "filename": graph_ref,
#             "label": spec["label"],
#             "domain": spec["domain"],
#             "is_builtin": True,
#         }

#     graphml_path = GRAPHS_DIR / graph_ref
#     if graphml_path.exists():
#         return graphml_path, {
#             "filename": graph_ref,
#             "label": graph_ref,
#             "domain": None,
#             "is_builtin": False,
#         }

#     raise HTTPException(404, f"Graph not found: {graph_ref}")


# def _load_graph_from_graphml(graphml_path: Path) -> tuple[list, list]:
#     """Parse a .graphml file directly with NetworkX and return nodes/edges lists."""
#     G = nx.read_graphml(str(graphml_path))
#     nodes = [
#         {"id": str(n), "label": str(d.get("label", n)), "type": str(d.get("type", "Other"))}
#         for n, d in G.nodes(data=True)
#     ]
#     edges = [
#         {"source": str(u), "target": str(v), "weight": float(d.get("weight", 0.7)),
#          "title": str(d.get("title", "")), "rnorm": str(d.get("rnorm", "related_to"))}
#         for u, v, d in G.edges(data=True)
#     ]
#     return nodes, edges


# def _load_graph_data(filename: str) -> tuple[list, list]:
#     """Load nodes and edges — prefer pre-built JSON, fall back to parsing the graphml."""
#     nodes_p, edges_p = _json_paths(filename)
#     if nodes_p.exists():
#         nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
#         edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
#         return nodes, edges

#     graphml_path = GRAPHS_DIR / filename
#     if graphml_path.exists():
#         return _load_graph_from_graphml(graphml_path)

#     return [], []


# def _load_graph_data(graph_ref: str) -> tuple[list, list]:
#     """Load nodes and edges, preferring cached JSON sidecars when available."""
#     graphml_path, meta = _resolve_graph_path(graph_ref)

#     if not meta["is_builtin"]:
#         nodes_p, edges_p = _json_paths(graph_ref)
#         if nodes_p.exists():
#             nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
#             edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
#             return nodes, edges

#     return _load_graph_from_graphml(graphml_path)


# def _make_job(initial_progress: str = "Starting…") -> str:
#     job_id = str(uuid.uuid4())[:8]
#     JOBS[job_id] = {
#         "status": "running",
#         "progress": initial_progress,
#         "result": None,
#         "error": None,
#     }
#     return job_id


# def _run_subprocess(cmd: list[str], job_id: str, on_done) -> None:
#     """Run a subprocess in a daemon thread, streaming stdout to job progress."""
#     def _worker():
#         try:
#             env = {**os.environ}
#             # ensure .env is sourced (run_pipeline and build_kg do their own load_env)
#             proc = subprocess.Popen(
#                 cmd, cwd=str(ROOT),
#                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
#                 text=True, env=env,
#             )
#             stderr_lines: list[str] = []
#             import selectors
#             sel = selectors.DefaultSelector()
#             sel.register(proc.stdout, selectors.EVENT_READ)
#             sel.register(proc.stderr, selectors.EVENT_READ)
#             while True:
#                 events = sel.select(timeout=1)
#                 for key, _ in events:
#                     line = key.fileobj.readline()
#                     if not line:
#                         continue
#                     line = line.rstrip()
#                     if key.fileobj is proc.stdout:
#                         if line:
#                             JOBS[job_id]["progress"] = line[:160]
#                     else:
#                         if line:
#                             stderr_lines.append(line)
#                             JOBS[job_id]["progress"] = f"[err] {line[:150]}"
#                 if proc.poll() is not None:
#                     # drain remaining output
#                     for line in proc.stdout:
#                         line = line.rstrip()
#                         if line:
#                             JOBS[job_id]["progress"] = line[:160]
#                     for line in proc.stderr:
#                         line = line.rstrip()
#                         if line:
#                             stderr_lines.append(line)
#                     break
#             sel.close()
#             if proc.returncode == 0:
#                 on_done(job_id)
#             else:
#                 err_detail = "\n".join(stderr_lines[-20:]) if stderr_lines else ""
#                 JOBS[job_id]["status"] = "failed"
#                 JOBS[job_id]["error"] = (
#                     f"Process exited with code {proc.returncode}"
#                     + (f"\n{err_detail}" if err_detail else "")
#                 )
#         except Exception as exc:
#             JOBS[job_id]["status"] = "failed"
#             JOBS[job_id]["error"] = str(exc)

#     threading.Thread(target=_worker, daemon=True).start()


# # ── Static / root ──────────────────────────────────────────────────────────────

# @app.get("/")
# async def serve_ui():
#     return FileResponse(UI_DIR / "index.html")


# @app.get("/graph")
# async def serve_graph():
#     return FileResponse(UI_DIR / "graph.html")


# # ── Graph list ─────────────────────────────────────────────────────────────────

# @app.get("/api/graphs")
# async def list_graphs():
#     """List all .graphml files with basic stats."""
#     result = []

#     for graph_id, spec in BUILTIN_GRAPHS.items():
#         _, builtin_path = _find_builtin_graph(graph_id)
#         if builtin_path is None:
#             continue
#         nodes, edges = _load_graph_data(graph_id)
#         result.append({
#             "filename": graph_id,
#             "label": spec["label"],
#             "domain": spec["domain"],
#             "modified": builtin_path.stat().st_mtime,
#             "nodes": len(nodes),
#             "edges": len(edges),
#             "is_builtin": True,
#         })

#     files = []
#     if GRAPHS_DIR.exists():
#         files = sorted(
#             GRAPHS_DIR.glob("*.graphml"),
#             key=lambda f: f.stat().st_mtime,
#             reverse=True,
#         )

#     for f in files:
#         nodes, edges = _load_graph_data(f.name)
#         result.append({
#             "filename": f.name,
#             "label": f.name,
#             "domain": None,
#             "modified": f.stat().st_mtime,
#             "nodes": len(nodes),
#             "edges": len(edges),
#             "is_builtin": False,
#         })

#     result.sort(key=lambda g: g["modified"], reverse=True)
#     return {"graphs": result}


# # ── Graph data (Cytoscape elements) ───────────────────────────────────────────

# @app.get("/api/graphs/{filename}")
# async def get_graph(filename: str, limit: int = 400):
#     """
#     Return graph stats + Cytoscape elements.
#     Only the top `limit` nodes by degree are returned to keep rendering fast.
#     """
#     nodes, edges = _load_graph_data(filename)
#     if not nodes:
#         raise HTTPException(404, f"No data found for graph: {filename}")
#     _, graph_meta = _resolve_graph_path(filename)

#     # Degree from edges
#     degree: dict[str, int] = {}
#     for e in edges:
#         s, t = e.get("source"), e.get("target")
#         if s:
#             degree[s] = degree.get(s, 0) + 1
#         if t:
#             degree[t] = degree.get(t, 0) + 1

#     # Top-N nodes by degree
#     top_nodes = sorted(nodes, key=lambda n: degree.get(n["id"], 0), reverse=True)[:limit]
#     top_ids = {n["id"] for n in top_nodes}

#     # Edges whose both endpoints are in the shown set
#     top_edges = [
#         e for e in edges
#         if e.get("source") in top_ids and e.get("target") in top_ids
#     ]

#     # Full-graph type counts
#     type_counts = dict(Counter(n.get("type", "Other") for n in nodes))

#     cy_nodes = [
#         {
#             "data": {
#                 "id": n["id"],
#                 "label": n.get("label", n["id"])[:50],
#                 "type": n.get("type", "Other"),
#                 "degree": degree.get(n["id"], 0),
#             }
#         }
#         for n in top_nodes
#     ]
#     cy_edges = [
#         {
#             "data": {
#                 "source": e["source"],
#                 "target": e["target"],
#                 "label": e.get("rnorm", e.get("title", "related_to")),
#                 "conf": float(e.get("weight", 0.7)),
#             }
#         }
#         for e in top_edges
#     ]

#     return {
#         "graph": graph_meta,
#         "stats": {
#             "nodes": len(nodes),
#             "edges": len(edges),
#             "shown_nodes": len(top_nodes),
#             "shown_edges": len(top_edges),
#             "node_types": type_counts,
#         },
#         "elements": cy_nodes + cy_edges,
#     }


# # ── Download graphml ───────────────────────────────────────────────────────────

# @app.get("/api/graphs/{filename}/download")
# async def download_graph(filename: str):
#     """Serve the raw .graphml file so the browser can download it directly."""
#     graphml_path, meta = _resolve_graph_path(filename)
#     download_name = f"{meta['label'].replace(' ', '_')}.graphml" if meta["is_builtin"] else filename
#     return FileResponse(
#         path=str(graphml_path),
#         filename=download_name,
#         media_type="application/xml",
#     )


# # ── Upload graphml ────────────────────────────────────────────────────────────

# @app.post("/api/graphs/upload")
# async def upload_graph(file: UploadFile = File(...)):
#     if not file.filename.endswith(".graphml"):
#         raise HTTPException(400, "Only .graphml files are accepted.")
#     GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
#     dest = GRAPHS_DIR / file.filename
#     dest.write_bytes(await file.read())
#     return {"filename": file.filename}


# class SaveAsBuiltinRequest(BaseModel):
#     label: str
#     domain: str


# @app.post("/api/graphs/{filename}/save-as-builtin")
# async def save_as_builtin(filename: str, req: SaveAsBuiltinRequest):
#     """Copy a non-builtin graph into UI/default_graphs/ and register it as a
#     built-in, persisted to disk so it survives a server restart. Refuses if
#     the filename is already a built-in ID (nothing to copy/promote)."""
#     if filename in BUILTIN_GRAPHS:
#         raise HTTPException(400, f"'{filename}' is already a built-in graph.")

#     src = GRAPHS_DIR / filename
#     if not src.exists():
#         raise HTTPException(404, f"Graph not found: {filename}")

#     slug = "".join(c if c.isalnum() else "_" for c in req.domain.lower()).strip("_") or "custom"
#     key = f"builtin-{slug}"
#     suffix = 2
#     base_key = key
#     while key in BUILTIN_GRAPHS:
#         key = f"{base_key}-{suffix}"
#         suffix += 1

#     DEFAULT_GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
#     dest = DEFAULT_GRAPHS_DIR / f"{slug}_kg_latest.graphml"
#     dest.write_bytes(src.read_bytes())

#     BUILTIN_GRAPHS[key] = {
#         "label": req.label,
#         "domain": req.domain,
#         "candidates": [dest],
#     }
#     _persist_custom_builtins()

#     return {"key": key, "label": req.label, "domain": req.domain}


# # ── Build KG ───────────────────────────────────────────────────────────────────

# @app.post("/api/graphs/build")
# async def build_graph(req: BuildKGRequest):
#     """Start a build_kg.py run in the background and return a job ID."""
#     job_id = _make_job("Preparing build…")

#     cmd = [
#         sys.executable, str(ROOT / "build_kg.py"),
#         "--max-per-query", str(req.max_papers),
#         "--domain", req.domain,
#     ]
#     if not req.use_s2:
#         cmd.append("--no-s2")
#     if not req.use_clinicaltrials:
#         cmd.append("--no-clinicaltrials")
#     if req.source_weights:
#         cmd += ["--source-weights", json.dumps(req.source_weights)]
#     if req.query_file:
#         query_path = QUERIES_UPLOAD_DIR / req.query_file
#         if query_path.exists():
#             cmd += ["--queries", str(query_path)]
#         else:
#             raise HTTPException(404, f"Uploaded query file not found: {req.query_file}")

#     def on_done(jid):
#         files = sorted(
#             GRAPHS_DIR.glob("*.graphml"),
#             key=lambda f: f.stat().st_mtime,
#             reverse=True,
#         )
#         JOBS[jid]["status"] = "done"
#         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

#     _run_subprocess(cmd, job_id, on_done)
#     return {"job_id": job_id}


# # ── Upload a search-query file (used by the Create Graph form) ────────────────

# @app.post("/api/queries/upload")
# async def upload_queries(file: UploadFile = File(...)):
#     if not (file.filename.endswith(".json") or file.filename.endswith(".txt")):
#         raise HTTPException(400, "Only .json or .txt query files are accepted.")
#     QUERIES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
#     dest = QUERIES_UPLOAD_DIR / file.filename
#     content = await file.read()
#     if file.filename.endswith(".json"):
#         try:
#             json.loads(content)
#         except Exception:
#             raise HTTPException(400, "That .json file isn't valid JSON.")
#     dest.write_bytes(content)
#     return {"filename": file.filename}


# # ── Enhance KG ─────────────────────────────────────────────────────────────────

# @app.post("/api/graphs/enhance")
# async def enhance_graph(req: EnhanceRequest):
#     """
#     Enhance an existing graph with new sources.
#     Currently supports keyword-based expansion (re-runs build_kg with new queries).
#     URL/file ingestion is a planned feature.
#     """
#     if req.enhance_type == "url":
#         if not req.url:
#             raise HTTPException(400, "Provide a url.")

#         nct_match = NCT_ID_RE.search(req.url)
#         if not nct_match:
#             # Only clinicaltrials.gov single-trial URLs are wired up today.
#             # Anything else (a generic paper URL, a news article, etc.) still
#             # needs real fetch/parse support that doesn't exist yet.
#             raise HTTPException(
#                 501,
#                 "URL ingestion currently only supports ClinicalTrials.gov trial "
#                 "links (containing an NCT ID, e.g. .../study/NCT01234567). "
#                 "General URL ingestion is not yet implemented.",
#             )

#         graph_path, _ = _resolve_graph_path(req.graph_filename)
#         job_id = _make_job(f"Fetching trial {nct_match.group(0)}…")

#         cmd = [
#             sys.executable, str(ROOT / "build_kg.py"),
#             "--nct-id", nct_match.group(0),
#             "--base-graph", str(graph_path),
#         ]

#         def on_done_url(jid):
#             files = sorted(
#                 GRAPHS_DIR.glob("*.graphml"),
#                 key=lambda f: f.stat().st_mtime,
#                 reverse=True,
#             )
#             JOBS[jid]["status"] = "done"
#             JOBS[jid]["result"] = {"filename": files[0].name if files else None}

#         _run_subprocess(cmd, job_id, on_done_url)
#         return {"job_id": job_id}

#     if req.enhance_type != "keywords" or not req.keywords:
#         raise HTTPException(400, "Provide enhance_type='keywords' and a non-empty keywords list.")

#     job_id = _make_job("Running targeted literature search…")

#     cmd = [
#         sys.executable, str(ROOT / "build_kg.py"),
#         "--max-per-query", str(req.max_papers),
#         "--no-s2",
#     ]

#     def on_done(jid):
#         files = sorted(
#             GRAPHS_DIR.glob("*.graphml"),
#             key=lambda f: f.stat().st_mtime,
#             reverse=True,
#         )
#         JOBS[jid]["status"] = "done"
#         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

#     _run_subprocess(cmd, job_id, on_done)
#     return {"job_id": job_id}


# # ── Hypothesis generation ──────────────────────────────────────────────────────

# @app.post("/api/hypothesis")
# async def generate_hypothesis(req: HypothesisRequest):
#     """Run the full SciAgents hypothesis pipeline and return a job ID."""
#     graph_path, _ = _resolve_graph_path(req.graph_filename)

#     job_id = _make_job("Initialising agents…")

#     cmd = [
#         sys.executable, str(ROOT / "run_pipeline.py"),
#         "--graph", str(graph_path),
#         "--query", req.query,
#     ]

#     def on_done(jid):
#         # Find the most recently written hypothesis .md
#         md_files = sorted(
#             list(GRAPHS_DIR.glob("hypothesis_run*.md"))
#             + list(OUTPUTS_DIR.rglob("hypothesis_run*.md")),
#             key=lambda f: f.stat().st_mtime,
#             reverse=True,
#         )
#         hypothesis_text = md_files[0].read_text(encoding="utf-8") if md_files else ""
#         JOBS[jid]["status"] = "done"
#         JOBS[jid]["result"] = {"hypothesis": hypothesis_text[:8000]}

#     _run_subprocess(cmd, job_id, on_done)
#     return {"job_id": job_id}


# # ── Focused path discovery ─────────────────────────────────────────────────────

# @app.post("/api/path-discovery")
# async def run_path_discovery(req: PathDiscoveryRequest):
#     """Run only graph-path discovery/ranking and return a job ID."""
#     graph_path, _ = _resolve_graph_path(req.graph_filename)

#     job_id = _make_job("Preparing path discovery…")
#     PATH_DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
#     out_txt = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.txt"
#     out_json = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.json"

#     cmd = [
#         sys.executable, str(ROOT / "run_path_discovery.py"),
#         "--graph", str(graph_path),
#         "--query", req.query,
#         "--source", req.source,
#         "--target-type", req.target_type,
#         "--domain", req.domain,
#         "--max-targets", str(req.max_targets),
#         "--top-n", str(req.top_n),
#         "--seed", str(req.seed),
#         "--logic-weight", str(req.logic_weight),
#         "--relevance-weight", str(req.relevance_weight),
#         "--novelty-weight", str(req.novelty_weight),
#         "--surprise-weight", str(req.surprise_weight),
#         "--rank-by", req.rank_by,
#         "--out", str(out_txt),
#         "--json-out", str(out_json),
#     ]
#     if req.skip_logic:
#         cmd.append("--skip-logic")

#     def on_done(jid):
#         result = {
#             "text": out_txt.read_text(encoding="utf-8") if out_txt.exists() else "",
#             "text_path": str(out_txt.relative_to(ROOT)) if out_txt.exists() else None,
#             "json_path": str(out_json.relative_to(ROOT)) if out_json.exists() else None,
#         }
#         if out_json.exists():
#             try:
#                 result["data"] = json.loads(out_json.read_text(encoding="utf-8"))
#             except Exception:
#                 result["data"] = None
#         JOBS[jid]["status"] = "done"
#         JOBS[jid]["result"] = result

#     _run_subprocess(cmd, job_id, on_done)
#     return {"job_id": job_id}


# # ── Job status ─────────────────────────────────────────────────────────────────

# @app.get("/api/jobs/{job_id}")
# async def get_job_status(job_id: str):
#     job = JOBS.get(job_id)
#     if not job:
#         raise HTTPException(404, "Job not found")
#     return job


# # ── Entry point ────────────────────────────────────────────────────────────────

# if __name__ == "__main__":
#     print(f"\n  SAGE UI  →  http://localhost:8000\n")
#     uvicorn.run(
#         "server:app",
#         host="0.0.0.0",
#         port=8000,
#         reload=True,
#         reload_dirs=[str(UI_DIR)],
#         app_dir=str(UI_DIR),
#     )


# # """
# # SAGE Hypothesis Generation Engine — FastAPI backend
# # Run from the project root:
# #     pip install fastapi uvicorn
# #     uvicorn UI.server:app --reload --port 8000
# # Or directly:
# #     python UI/server.py
# # """

# # from __future__ import annotations

# # import json
# # import os
# # import re
# # import subprocess
# # import sys
# # import threading
# # import uuid
# # from collections import Counter
# # from pathlib import Path
# # from typing import List, Optional

# # import networkx as nx
# # import uvicorn
# # from fastapi import FastAPI, HTTPException, UploadFile, File
# # from fastapi.responses import FileResponse, JSONResponse
# # from pydantic import BaseModel

# # # ── Paths ─────────────────────────────────────────────────────────────────────
# # ROOT      = Path(__file__).parent.parent          # project root
# # UI_DIR    = Path(__file__).parent                 # UI/
# # GRAPHS_DIR = ROOT / "outputs" / "sage_kg"
# # OUTPUTS_DIR = ROOT / "outputs"
# # DEFAULT_GRAPHS_DIR = UI_DIR / "default_graphs"
# # INITIAL_GRAPH_DIR = ROOT / "inputs" / "initial_graph_biomed"
# # PATH_DISCOVERY_DIR = ROOT / "path_discovery"
# # QUERIES_UPLOAD_DIR = ROOT / "keywords" / "uploads"

# # NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)

# # BUILTIN_GRAPHS = {
# #     "builtin-bladder-cancer": {
# #         "label": "Bladder Cancer (built-in)",
# #         "domain": "bladder cancer",
# #         "candidates": [
# #             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg_latest.graphml",
# #             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg.graphml",
# #             INITIAL_GRAPH_DIR / "merged_KG.graphml",
# #         ],
# #     },
# #     "builtin-breast-cancer": {
# #         "label": "Breast Cancer (built-in)",
# #         "domain": "breast cancer",
# #         "candidates": [
# #             DEFAULT_GRAPHS_DIR / "breast_cancer_kg_latest.graphml",
# #             DEFAULT_GRAPHS_DIR / "breast_cancer_kg.graphml",
# #             INITIAL_GRAPH_DIR / "BreastCancer_separated.graphml",
# #         ],
# #     },
# #     "builtin-prostate-cancer": {
# #         "label": "Prostate Cancer (built-in)",
# #         "domain": "prostate cancer",
# #         "candidates": [
# #             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
# #             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg.graphml",
# #             GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
# #             GRAPHS_DIR / "prostate_cancer_kg.graphml",
# #         ],
# #     },
# # }

# # BUILTIN_GRAPHS_FILE = UI_DIR / "builtin_graphs.json"


# # def _load_custom_builtins() -> None:
# #     """Merge any user-saved 'built-in' graphs (from the Save as built-in
# #     action) into BUILTIN_GRAPHS. Runs once at startup so custom built-ins
# #     survive a server restart — the hardcoded three above are never touched
# #     or overwritten by this."""
# #     if not BUILTIN_GRAPHS_FILE.exists():
# #         return
# #     try:
# #         saved = json.loads(BUILTIN_GRAPHS_FILE.read_text(encoding="utf-8"))
# #         for key, spec in saved.items():
# #             BUILTIN_GRAPHS[key] = {
# #                 "label": spec["label"],
# #                 "domain": spec["domain"],
# #                 "candidates": [Path(p) for p in spec["candidates"]],
# #             }
# #     except Exception as e:
# #         print(f"[builtin_graphs] failed to load {BUILTIN_GRAPHS_FILE}: {e}")


# # def _persist_custom_builtins() -> None:
# #     """Write every BUILTIN_GRAPHS entry that isn't one of the three original
# #     hardcoded ones out to disk, so custom built-ins survive a restart."""
# #     original_keys = {"builtin-bladder-cancer", "builtin-breast-cancer", "builtin-prostate-cancer"}
# #     custom = {
# #         key: {
# #             "label": spec["label"],
# #             "domain": spec["domain"],
# #             "candidates": [str(p) for p in spec["candidates"]],
# #         }
# #         for key, spec in BUILTIN_GRAPHS.items()
# #         if key not in original_keys
# #     }
# #     BUILTIN_GRAPHS_FILE.parent.mkdir(parents=True, exist_ok=True)
# #     BUILTIN_GRAPHS_FILE.write_text(json.dumps(custom, indent=2), encoding="utf-8")


# # _load_custom_builtins()

# # app = FastAPI(title="SAGE Hypothesis Generation Engine", version="1.0")

# # # ── In-memory job store ────────────────────────────────────────────────────────
# # JOBS: dict[str, dict] = {}


# # # ── Pydantic models ────────────────────────────────────────────────────────────

# # class BuildKGRequest(BaseModel):
# #     domain: str = "prostate cancer"
# #     keywords: List[str] = []
# #     max_papers: int = 50
# #     conf_threshold: float = 0.5
# #     use_s2: bool = False
# #     use_clinicaltrials: bool = True
# #     source_weights: Optional[dict[str, float]] = None
# #     query_file: Optional[str] = None  # filename returned by /api/queries/upload

# # class HypothesisRequest(BaseModel):
# #     graph_filename: str
# #     query: str

# # class PathDiscoveryRequest(BaseModel):
# #     graph_filename: str
# #     query: str = "Find underexplored prognostic biomarkers in prostate cancer."
# #     source: str = ""
# #     target_type: str = ""
# #     domain: str = "prostate cancer"
# #     max_targets: int = 30
# #     top_n: int = 10
# #     seed: int = 7
# #     logic_weight: float = 0.2
# #     relevance_weight: float = 0.2
# #     novelty_weight: float = 0.3
# #     surprise_weight: float = 0.3
# #     rank_by: str = "total"
# #     skip_logic: bool = False

# # class EnhanceRequest(BaseModel):
# #     graph_filename: str
# #     enhance_type: str          # "url" | "keywords"
# #     url: Optional[str] = None
# #     keywords: Optional[List[str]] = None
# #     source_types: Optional[List[str]] = None
# #     max_papers: int = 20


# # # ── Helpers ────────────────────────────────────────────────────────────────────

# # def _json_paths(filename: str) -> tuple[Path, Path]:
# #     """Return the nodes/edges JSON paths for a given .graphml filename."""
# #     stem = filename.replace(".graphml", "")
# #     base = GRAPHS_DIR / stem
# #     return Path(str(base) + "_nodes.json"), Path(str(base) + "_edges.json")


# # def _find_builtin_graph(graph_id: str) -> tuple[dict | None, Path | None]:
# #     spec = BUILTIN_GRAPHS.get(graph_id)
# #     if not spec:
# #         return None, None

# #     for candidate in spec["candidates"]:
# #         if candidate.exists():
# #             return spec, candidate
# #     return spec, None


# # def _resolve_graph_path(graph_ref: str) -> tuple[Path, dict]:
# #     """Resolve either a built-in graph ID or a user-uploaded graph filename."""
# #     spec, builtin_path = _find_builtin_graph(graph_ref)
# #     if spec is not None:
# #         if builtin_path is None:
# #             raise HTTPException(404, f"Built-in graph is not available: {graph_ref}")
# #         return builtin_path, {
# #             "filename": graph_ref,
# #             "label": spec["label"],
# #             "domain": spec["domain"],
# #             "is_builtin": True,
# #         }

# #     graphml_path = GRAPHS_DIR / graph_ref
# #     if graphml_path.exists():
# #         return graphml_path, {
# #             "filename": graph_ref,
# #             "label": graph_ref,
# #             "domain": None,
# #             "is_builtin": False,
# #         }

# #     raise HTTPException(404, f"Graph not found: {graph_ref}")


# # def _load_graph_from_graphml(graphml_path: Path) -> tuple[list, list]:
# #     """Parse a .graphml file directly with NetworkX and return nodes/edges lists."""
# #     G = nx.read_graphml(str(graphml_path))
# #     nodes = [
# #         {"id": str(n), "label": str(d.get("label", n)), "type": str(d.get("type", "Other"))}
# #         for n, d in G.nodes(data=True)
# #     ]
# #     edges = [
# #         {"source": str(u), "target": str(v), "weight": float(d.get("weight", 0.7)),
# #          "title": str(d.get("title", "")), "rnorm": str(d.get("rnorm", "related_to"))}
# #         for u, v, d in G.edges(data=True)
# #     ]
# #     return nodes, edges


# # def _load_graph_data(filename: str) -> tuple[list, list]:
# #     """Load nodes and edges — prefer pre-built JSON, fall back to parsing the graphml."""
# #     nodes_p, edges_p = _json_paths(filename)
# #     if nodes_p.exists():
# #         nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
# #         edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
# #         return nodes, edges

# #     graphml_path = GRAPHS_DIR / filename
# #     if graphml_path.exists():
# #         return _load_graph_from_graphml(graphml_path)

# #     return [], []


# # def _load_graph_data(graph_ref: str) -> tuple[list, list]:
# #     """Load nodes and edges, preferring cached JSON sidecars when available."""
# #     graphml_path, meta = _resolve_graph_path(graph_ref)

# #     if not meta["is_builtin"]:
# #         nodes_p, edges_p = _json_paths(graph_ref)
# #         if nodes_p.exists():
# #             nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
# #             edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
# #             return nodes, edges

# #     return _load_graph_from_graphml(graphml_path)


# # def _make_job(initial_progress: str = "Starting…") -> str:
# #     job_id = str(uuid.uuid4())[:8]
# #     JOBS[job_id] = {
# #         "status": "running",
# #         "progress": initial_progress,
# #         "result": None,
# #         "error": None,
# #     }
# #     return job_id


# # def _run_subprocess(cmd: list[str], job_id: str, on_done) -> None:
# #     """Run a subprocess in a daemon thread, streaming stdout to job progress."""
# #     def _worker():
# #         try:
# #             env = {**os.environ}
# #             # ensure .env is sourced (run_pipeline and build_kg do their own load_env)
# #             proc = subprocess.Popen(
# #                 cmd, cwd=str(ROOT),
# #                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
# #                 text=True, env=env,
# #             )
# #             stderr_lines: list[str] = []
# #             import selectors
# #             sel = selectors.DefaultSelector()
# #             sel.register(proc.stdout, selectors.EVENT_READ)
# #             sel.register(proc.stderr, selectors.EVENT_READ)
# #             while True:
# #                 events = sel.select(timeout=1)
# #                 for key, _ in events:
# #                     line = key.fileobj.readline()
# #                     if not line:
# #                         continue
# #                     line = line.rstrip()
# #                     if key.fileobj is proc.stdout:
# #                         if line:
# #                             JOBS[job_id]["progress"] = line[:160]
# #                     else:
# #                         if line:
# #                             stderr_lines.append(line)
# #                             JOBS[job_id]["progress"] = f"[err] {line[:150]}"
# #                 if proc.poll() is not None:
# #                     # drain remaining output
# #                     for line in proc.stdout:
# #                         line = line.rstrip()
# #                         if line:
# #                             JOBS[job_id]["progress"] = line[:160]
# #                     for line in proc.stderr:
# #                         line = line.rstrip()
# #                         if line:
# #                             stderr_lines.append(line)
# #                     break
# #             sel.close()
# #             if proc.returncode == 0:
# #                 on_done(job_id)
# #             else:
# #                 err_detail = "\n".join(stderr_lines[-20:]) if stderr_lines else ""
# #                 JOBS[job_id]["status"] = "failed"
# #                 JOBS[job_id]["error"] = (
# #                     f"Process exited with code {proc.returncode}"
# #                     + (f"\n{err_detail}" if err_detail else "")
# #                 )
# #         except Exception as exc:
# #             JOBS[job_id]["status"] = "failed"
# #             JOBS[job_id]["error"] = str(exc)

# #     threading.Thread(target=_worker, daemon=True).start()


# # # ── Static / root ──────────────────────────────────────────────────────────────

# # @app.get("/")
# # async def serve_ui():
# #     return FileResponse(UI_DIR / "index.html")


# # @app.get("/graph")
# # async def serve_graph():
# #     return FileResponse(UI_DIR / "graph.html")


# # # ── Graph list ─────────────────────────────────────────────────────────────────

# # @app.get("/api/graphs")
# # async def list_graphs():
# #     """List all .graphml files with basic stats."""
# #     result = []

# #     for graph_id, spec in BUILTIN_GRAPHS.items():
# #         _, builtin_path = _find_builtin_graph(graph_id)
# #         if builtin_path is None:
# #             continue
# #         nodes, edges = _load_graph_data(graph_id)
# #         result.append({
# #             "filename": graph_id,
# #             "label": spec["label"],
# #             "domain": spec["domain"],
# #             "modified": builtin_path.stat().st_mtime,
# #             "nodes": len(nodes),
# #             "edges": len(edges),
# #             "is_builtin": True,
# #         })

# #     files = []
# #     if GRAPHS_DIR.exists():
# #         files = sorted(
# #             GRAPHS_DIR.glob("*.graphml"),
# #             key=lambda f: f.stat().st_mtime,
# #             reverse=True,
# #         )

# #     for f in files:
# #         nodes, edges = _load_graph_data(f.name)
# #         result.append({
# #             "filename": f.name,
# #             "label": f.name,
# #             "domain": None,
# #             "modified": f.stat().st_mtime,
# #             "nodes": len(nodes),
# #             "edges": len(edges),
# #             "is_builtin": False,
# #         })

# #     result.sort(key=lambda g: g["modified"], reverse=True)
# #     return {"graphs": result}


# # # ── Graph data (Cytoscape elements) ───────────────────────────────────────────

# # @app.get("/api/graphs/{filename}")
# # async def get_graph(filename: str, limit: int = 400):
# #     """
# #     Return graph stats + Cytoscape elements.
# #     Only the top `limit` nodes by degree are returned to keep rendering fast.
# #     """
# #     nodes, edges = _load_graph_data(filename)
# #     if not nodes:
# #         raise HTTPException(404, f"No data found for graph: {filename}")
# #     _, graph_meta = _resolve_graph_path(filename)

# #     # Degree from edges
# #     degree: dict[str, int] = {}
# #     for e in edges:
# #         s, t = e.get("source"), e.get("target")
# #         if s:
# #             degree[s] = degree.get(s, 0) + 1
# #         if t:
# #             degree[t] = degree.get(t, 0) + 1

# #     # Top-N nodes by degree
# #     top_nodes = sorted(nodes, key=lambda n: degree.get(n["id"], 0), reverse=True)[:limit]
# #     top_ids = {n["id"] for n in top_nodes}

# #     # Edges whose both endpoints are in the shown set
# #     top_edges = [
# #         e for e in edges
# #         if e.get("source") in top_ids and e.get("target") in top_ids
# #     ]

# #     # Full-graph type counts
# #     type_counts = dict(Counter(n.get("type", "Other") for n in nodes))

# #     cy_nodes = [
# #         {
# #             "data": {
# #                 "id": n["id"],
# #                 "label": n.get("label", n["id"])[:50],
# #                 "type": n.get("type", "Other"),
# #                 "degree": degree.get(n["id"], 0),
# #             }
# #         }
# #         for n in top_nodes
# #     ]
# #     cy_edges = [
# #         {
# #             "data": {
# #                 "source": e["source"],
# #                 "target": e["target"],
# #                 "label": e.get("rnorm", e.get("title", "related_to")),
# #                 "conf": float(e.get("weight", 0.7)),
# #             }
# #         }
# #         for e in top_edges
# #     ]

# #     return {
# #         "graph": graph_meta,
# #         "stats": {
# #             "nodes": len(nodes),
# #             "edges": len(edges),
# #             "shown_nodes": len(top_nodes),
# #             "shown_edges": len(top_edges),
# #             "node_types": type_counts,
# #         },
# #         "elements": cy_nodes + cy_edges,
# #     }


# # # ── Upload graphml ────────────────────────────────────────────────────────────

# # @app.post("/api/graphs/upload")
# # async def upload_graph(file: UploadFile = File(...)):
# #     if not file.filename.endswith(".graphml"):
# #         raise HTTPException(400, "Only .graphml files are accepted.")
# #     GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
# #     dest = GRAPHS_DIR / file.filename
# #     dest.write_bytes(await file.read())
# #     return {"filename": file.filename}


# # class SaveAsBuiltinRequest(BaseModel):
# #     label: str
# #     domain: str


# # @app.post("/api/graphs/{filename}/save-as-builtin")
# # async def save_as_builtin(filename: str, req: SaveAsBuiltinRequest):
# #     """Copy a non-builtin graph into UI/default_graphs/ and register it as a
# #     built-in, persisted to disk so it survives a server restart. Refuses if
# #     the filename is already a built-in ID (nothing to copy/promote)."""
# #     if filename in BUILTIN_GRAPHS:
# #         raise HTTPException(400, f"'{filename}' is already a built-in graph.")

# #     src = GRAPHS_DIR / filename
# #     if not src.exists():
# #         raise HTTPException(404, f"Graph not found: {filename}")

# #     slug = "".join(c if c.isalnum() else "_" for c in req.domain.lower()).strip("_") or "custom"
# #     key = f"builtin-{slug}"
# #     suffix = 2
# #     base_key = key
# #     while key in BUILTIN_GRAPHS:
# #         key = f"{base_key}-{suffix}"
# #         suffix += 1

# #     DEFAULT_GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
# #     dest = DEFAULT_GRAPHS_DIR / f"{slug}_kg_latest.graphml"
# #     dest.write_bytes(src.read_bytes())

# #     BUILTIN_GRAPHS[key] = {
# #         "label": req.label,
# #         "domain": req.domain,
# #         "candidates": [dest],
# #     }
# #     _persist_custom_builtins()

# #     return {"key": key, "label": req.label, "domain": req.domain}


# # # ── Build KG ───────────────────────────────────────────────────────────────────

# # @app.post("/api/graphs/build")
# # async def build_graph(req: BuildKGRequest):
# #     """Start a build_kg.py run in the background and return a job ID."""
# #     job_id = _make_job("Preparing build…")

# #     cmd = [
# #         sys.executable, str(ROOT / "build_kg.py"),
# #         "--max-per-query", str(req.max_papers),
# #         "--domain", req.domain,
# #     ]
# #     if not req.use_s2:
# #         cmd.append("--no-s2")
# #     if not req.use_clinicaltrials:
# #         cmd.append("--no-clinicaltrials")
# #     if req.source_weights:
# #         cmd += ["--source-weights", json.dumps(req.source_weights)]
# #     if req.query_file:
# #         query_path = QUERIES_UPLOAD_DIR / req.query_file
# #         if query_path.exists():
# #             cmd += ["--queries", str(query_path)]
# #         else:
# #             raise HTTPException(404, f"Uploaded query file not found: {req.query_file}")

# #     def on_done(jid):
# #         files = sorted(
# #             GRAPHS_DIR.glob("*.graphml"),
# #             key=lambda f: f.stat().st_mtime,
# #             reverse=True,
# #         )
# #         JOBS[jid]["status"] = "done"
# #         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# #     _run_subprocess(cmd, job_id, on_done)
# #     return {"job_id": job_id}


# # # ── Upload a search-query file (used by the Create Graph form) ────────────────

# # @app.post("/api/queries/upload")
# # async def upload_queries(file: UploadFile = File(...)):
# #     if not (file.filename.endswith(".json") or file.filename.endswith(".txt")):
# #         raise HTTPException(400, "Only .json or .txt query files are accepted.")
# #     QUERIES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# #     dest = QUERIES_UPLOAD_DIR / file.filename
# #     content = await file.read()
# #     if file.filename.endswith(".json"):
# #         try:
# #             json.loads(content)
# #         except Exception:
# #             raise HTTPException(400, "That .json file isn't valid JSON.")
# #     dest.write_bytes(content)
# #     return {"filename": file.filename}


# # # ── Enhance KG ─────────────────────────────────────────────────────────────────

# # @app.post("/api/graphs/enhance")
# # async def enhance_graph(req: EnhanceRequest):
# #     """
# #     Enhance an existing graph with new sources.
# #     Currently supports keyword-based expansion (re-runs build_kg with new queries).
# #     URL/file ingestion is a planned feature.
# #     """
# #     if req.enhance_type == "url":
# #         if not req.url:
# #             raise HTTPException(400, "Provide a url.")

# #         nct_match = NCT_ID_RE.search(req.url)
# #         if not nct_match:
# #             # Only clinicaltrials.gov single-trial URLs are wired up today.
# #             # Anything else (a generic paper URL, a news article, etc.) still
# #             # needs real fetch/parse support that doesn't exist yet.
# #             raise HTTPException(
# #                 501,
# #                 "URL ingestion currently only supports ClinicalTrials.gov trial "
# #                 "links (containing an NCT ID, e.g. .../study/NCT01234567). "
# #                 "General URL ingestion is not yet implemented.",
# #             )

# #         graph_path, _ = _resolve_graph_path(req.graph_filename)
# #         job_id = _make_job(f"Fetching trial {nct_match.group(0)}…")

# #         cmd = [
# #             sys.executable, str(ROOT / "build_kg.py"),
# #             "--nct-id", nct_match.group(0),
# #             "--base-graph", str(graph_path),
# #         ]

# #         def on_done_url(jid):
# #             files = sorted(
# #                 GRAPHS_DIR.glob("*.graphml"),
# #                 key=lambda f: f.stat().st_mtime,
# #                 reverse=True,
# #             )
# #             JOBS[jid]["status"] = "done"
# #             JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# #         _run_subprocess(cmd, job_id, on_done_url)
# #         return {"job_id": job_id}

# #     if req.enhance_type != "keywords" or not req.keywords:
# #         raise HTTPException(400, "Provide enhance_type='keywords' and a non-empty keywords list.")

# #     job_id = _make_job("Running targeted literature search…")

# #     cmd = [
# #         sys.executable, str(ROOT / "build_kg.py"),
# #         "--max-per-query", str(req.max_papers),
# #         "--no-s2",
# #     ]

# #     def on_done(jid):
# #         files = sorted(
# #             GRAPHS_DIR.glob("*.graphml"),
# #             key=lambda f: f.stat().st_mtime,
# #             reverse=True,
# #         )
# #         JOBS[jid]["status"] = "done"
# #         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# #     _run_subprocess(cmd, job_id, on_done)
# #     return {"job_id": job_id}


# # # ── Hypothesis generation ──────────────────────────────────────────────────────

# # @app.post("/api/hypothesis")
# # async def generate_hypothesis(req: HypothesisRequest):
# #     """Run the full SciAgents hypothesis pipeline and return a job ID."""
# #     graph_path, _ = _resolve_graph_path(req.graph_filename)

# #     job_id = _make_job("Initialising agents…")

# #     cmd = [
# #         sys.executable, str(ROOT / "run_pipeline.py"),
# #         "--graph", str(graph_path),
# #         "--query", req.query,
# #     ]

# #     def on_done(jid):
# #         # Find the most recently written hypothesis .md
# #         md_files = sorted(
# #             list(GRAPHS_DIR.glob("hypothesis_run*.md"))
# #             + list(OUTPUTS_DIR.rglob("hypothesis_run*.md")),
# #             key=lambda f: f.stat().st_mtime,
# #             reverse=True,
# #         )
# #         hypothesis_text = md_files[0].read_text(encoding="utf-8") if md_files else ""
# #         JOBS[jid]["status"] = "done"
# #         JOBS[jid]["result"] = {"hypothesis": hypothesis_text[:8000]}

# #     _run_subprocess(cmd, job_id, on_done)
# #     return {"job_id": job_id}


# # # ── Focused path discovery ─────────────────────────────────────────────────────

# # @app.post("/api/path-discovery")
# # async def run_path_discovery(req: PathDiscoveryRequest):
# #     """Run only graph-path discovery/ranking and return a job ID."""
# #     graph_path, _ = _resolve_graph_path(req.graph_filename)

# #     job_id = _make_job("Preparing path discovery…")
# #     PATH_DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
# #     out_txt = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.txt"
# #     out_json = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.json"

# #     cmd = [
# #         sys.executable, str(ROOT / "run_path_discovery.py"),
# #         "--graph", str(graph_path),
# #         "--query", req.query,
# #         "--source", req.source,
# #         "--target-type", req.target_type,
# #         "--domain", req.domain,
# #         "--max-targets", str(req.max_targets),
# #         "--top-n", str(req.top_n),
# #         "--seed", str(req.seed),
# #         "--logic-weight", str(req.logic_weight),
# #         "--relevance-weight", str(req.relevance_weight),
# #         "--novelty-weight", str(req.novelty_weight),
# #         "--surprise-weight", str(req.surprise_weight),
# #         "--rank-by", req.rank_by,
# #         "--out", str(out_txt),
# #         "--json-out", str(out_json),
# #     ]
# #     if req.skip_logic:
# #         cmd.append("--skip-logic")

# #     def on_done(jid):
# #         result = {
# #             "text": out_txt.read_text(encoding="utf-8") if out_txt.exists() else "",
# #             "text_path": str(out_txt.relative_to(ROOT)) if out_txt.exists() else None,
# #             "json_path": str(out_json.relative_to(ROOT)) if out_json.exists() else None,
# #         }
# #         if out_json.exists():
# #             try:
# #                 result["data"] = json.loads(out_json.read_text(encoding="utf-8"))
# #             except Exception:
# #                 result["data"] = None
# #         JOBS[jid]["status"] = "done"
# #         JOBS[jid]["result"] = result

# #     _run_subprocess(cmd, job_id, on_done)
# #     return {"job_id": job_id}


# # # ── Job status ─────────────────────────────────────────────────────────────────

# # @app.get("/api/jobs/{job_id}")
# # async def get_job_status(job_id: str):
# #     job = JOBS.get(job_id)
# #     if not job:
# #         raise HTTPException(404, "Job not found")
# #     return job


# # # ── Entry point ────────────────────────────────────────────────────────────────

# # if __name__ == "__main__":
# #     print(f"\n  SAGE UI  →  http://localhost:8000\n")
# #     uvicorn.run(
# #         "server:app",
# #         host="0.0.0.0",
# #         port=8000,
# #         reload=True,
# #         reload_dirs=[str(UI_DIR)],
# #         app_dir=str(UI_DIR),
# #     )



# # # """
# # # SAGE Hypothesis Generation Engine — FastAPI backend
# # # Run from the project root:
# # #     pip install fastapi uvicorn
# # #     uvicorn UI.server:app --reload --port 8000
# # # Or directly:
# # #     python UI/server.py
# # # """

# # # from __future__ import annotations

# # # import json
# # # import os
# # # import re
# # # import subprocess
# # # import sys
# # # import threading
# # # import uuid
# # # from collections import Counter
# # # from pathlib import Path
# # # from typing import List, Optional

# # # import networkx as nx
# # # import uvicorn
# # # from fastapi import FastAPI, HTTPException, UploadFile, File
# # # from fastapi.responses import FileResponse, JSONResponse
# # # from pydantic import BaseModel

# # # # ── Paths ─────────────────────────────────────────────────────────────────────
# # # ROOT      = Path(__file__).parent.parent          # project root
# # # UI_DIR    = Path(__file__).parent                 # UI/
# # # GRAPHS_DIR = ROOT / "outputs" / "sage_kg"
# # # OUTPUTS_DIR = ROOT / "outputs"
# # # DEFAULT_GRAPHS_DIR = UI_DIR / "default_graphs"
# # # INITIAL_GRAPH_DIR = ROOT / "inputs" / "initial_graph_biomed"
# # # PATH_DISCOVERY_DIR = ROOT / "path_discovery"
# # # QUERIES_UPLOAD_DIR = ROOT / "keywords" / "uploads"

# # # NCT_ID_RE = re.compile(r"NCT\d{8}", re.IGNORECASE)

# # # BUILTIN_GRAPHS = {
# # #     "builtin-bladder-cancer": {
# # #         "label": "Bladder Cancer (built-in)",
# # #         "domain": "bladder cancer",
# # #         "candidates": [
# # #             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg_latest.graphml",
# # #             DEFAULT_GRAPHS_DIR / "bladder_cancer_kg.graphml",
# # #             INITIAL_GRAPH_DIR / "merged_KG.graphml",
# # #         ],
# # #     },
# # #     "builtin-breast-cancer": {
# # #         "label": "Breast Cancer (built-in)",
# # #         "domain": "breast cancer",
# # #         "candidates": [
# # #             DEFAULT_GRAPHS_DIR / "breast_cancer_kg_latest.graphml",
# # #             DEFAULT_GRAPHS_DIR / "breast_cancer_kg.graphml",
# # #             INITIAL_GRAPH_DIR / "BreastCancer_separated.graphml",
# # #         ],
# # #     },
# # #     "builtin-prostate-cancer": {
# # #         "label": "Prostate Cancer (built-in)",
# # #         "domain": "prostate cancer",
# # #         "candidates": [
# # #             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
# # #             DEFAULT_GRAPHS_DIR / "prostate_cancer_kg.graphml",
# # #             GRAPHS_DIR / "prostate_cancer_kg_latest.graphml",
# # #             GRAPHS_DIR / "prostate_cancer_kg.graphml",
# # #         ],
# # #     },
# # # }

# # # app = FastAPI(title="SAGE Hypothesis Generation Engine", version="1.0")

# # # # ── In-memory job store ────────────────────────────────────────────────────────
# # # JOBS: dict[str, dict] = {}


# # # # ── Pydantic models ────────────────────────────────────────────────────────────

# # # class BuildKGRequest(BaseModel):
# # #     domain: str = "prostate cancer"
# # #     keywords: List[str] = []
# # #     max_papers: int = 50
# # #     conf_threshold: float = 0.5
# # #     use_s2: bool = False
# # #     use_clinicaltrials: bool = True
# # #     source_weights: Optional[dict[str, float]] = None
# # #     query_file: Optional[str] = None  # filename returned by /api/queries/upload

# # # class HypothesisRequest(BaseModel):
# # #     graph_filename: str
# # #     query: str

# # # class PathDiscoveryRequest(BaseModel):
# # #     graph_filename: str
# # #     query: str = "Find underexplored prognostic biomarkers in prostate cancer."
# # #     source: str = ""
# # #     target_type: str = ""
# # #     domain: str = "prostate cancer"
# # #     max_targets: int = 30
# # #     top_n: int = 10
# # #     seed: int = 7
# # #     logic_weight: float = 0.2
# # #     relevance_weight: float = 0.2
# # #     novelty_weight: float = 0.3
# # #     surprise_weight: float = 0.3
# # #     rank_by: str = "total"
# # #     skip_logic: bool = False

# # # class EnhanceRequest(BaseModel):
# # #     graph_filename: str
# # #     enhance_type: str          # "url" | "keywords"
# # #     url: Optional[str] = None
# # #     keywords: Optional[List[str]] = None
# # #     source_types: Optional[List[str]] = None
# # #     max_papers: int = 20


# # # # ── Helpers ────────────────────────────────────────────────────────────────────

# # # def _json_paths(filename: str) -> tuple[Path, Path]:
# # #     """Return the nodes/edges JSON paths for a given .graphml filename."""
# # #     stem = filename.replace(".graphml", "")
# # #     base = GRAPHS_DIR / stem
# # #     return Path(str(base) + "_nodes.json"), Path(str(base) + "_edges.json")


# # # def _find_builtin_graph(graph_id: str) -> tuple[dict | None, Path | None]:
# # #     spec = BUILTIN_GRAPHS.get(graph_id)
# # #     if not spec:
# # #         return None, None

# # #     for candidate in spec["candidates"]:
# # #         if candidate.exists():
# # #             return spec, candidate
# # #     return spec, None


# # # def _resolve_graph_path(graph_ref: str) -> tuple[Path, dict]:
# # #     """Resolve either a built-in graph ID or a user-uploaded graph filename."""
# # #     spec, builtin_path = _find_builtin_graph(graph_ref)
# # #     if spec is not None:
# # #         if builtin_path is None:
# # #             raise HTTPException(404, f"Built-in graph is not available: {graph_ref}")
# # #         return builtin_path, {
# # #             "filename": graph_ref,
# # #             "label": spec["label"],
# # #             "domain": spec["domain"],
# # #             "is_builtin": True,
# # #         }

# # #     graphml_path = GRAPHS_DIR / graph_ref
# # #     if graphml_path.exists():
# # #         return graphml_path, {
# # #             "filename": graph_ref,
# # #             "label": graph_ref,
# # #             "domain": None,
# # #             "is_builtin": False,
# # #         }

# # #     raise HTTPException(404, f"Graph not found: {graph_ref}")


# # # def _load_graph_from_graphml(graphml_path: Path) -> tuple[list, list]:
# # #     """Parse a .graphml file directly with NetworkX and return nodes/edges lists."""
# # #     G = nx.read_graphml(str(graphml_path))
# # #     nodes = [
# # #         {"id": str(n), "label": str(d.get("label", n)), "type": str(d.get("type", "Other"))}
# # #         for n, d in G.nodes(data=True)
# # #     ]
# # #     edges = [
# # #         {"source": str(u), "target": str(v), "weight": float(d.get("weight", 0.7)),
# # #          "title": str(d.get("title", "")), "rnorm": str(d.get("rnorm", "related_to"))}
# # #         for u, v, d in G.edges(data=True)
# # #     ]
# # #     return nodes, edges


# # # def _load_graph_data(filename: str) -> tuple[list, list]:
# # #     """Load nodes and edges — prefer pre-built JSON, fall back to parsing the graphml."""
# # #     nodes_p, edges_p = _json_paths(filename)
# # #     if nodes_p.exists():
# # #         nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
# # #         edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
# # #         return nodes, edges

# # #     graphml_path = GRAPHS_DIR / filename
# # #     if graphml_path.exists():
# # #         return _load_graph_from_graphml(graphml_path)

# # #     return [], []


# # # def _load_graph_data(graph_ref: str) -> tuple[list, list]:
# # #     """Load nodes and edges, preferring cached JSON sidecars when available."""
# # #     graphml_path, meta = _resolve_graph_path(graph_ref)

# # #     if not meta["is_builtin"]:
# # #         nodes_p, edges_p = _json_paths(graph_ref)
# # #         if nodes_p.exists():
# # #             nodes = json.loads(nodes_p.read_text(encoding="utf-8"))
# # #             edges = json.loads(edges_p.read_text(encoding="utf-8")) if edges_p.exists() else []
# # #             return nodes, edges

# # #     return _load_graph_from_graphml(graphml_path)


# # # def _make_job(initial_progress: str = "Starting…") -> str:
# # #     job_id = str(uuid.uuid4())[:8]
# # #     JOBS[job_id] = {
# # #         "status": "running",
# # #         "progress": initial_progress,
# # #         "result": None,
# # #         "error": None,
# # #     }
# # #     return job_id


# # # def _run_subprocess(cmd: list[str], job_id: str, on_done) -> None:
# # #     """Run a subprocess in a daemon thread, streaming stdout to job progress."""
# # #     def _worker():
# # #         try:
# # #             env = {**os.environ}
# # #             # ensure .env is sourced (run_pipeline and build_kg do their own load_env)
# # #             proc = subprocess.Popen(
# # #                 cmd, cwd=str(ROOT),
# # #                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
# # #                 text=True, env=env,
# # #             )
# # #             stderr_lines: list[str] = []
# # #             import selectors
# # #             sel = selectors.DefaultSelector()
# # #             sel.register(proc.stdout, selectors.EVENT_READ)
# # #             sel.register(proc.stderr, selectors.EVENT_READ)
# # #             while True:
# # #                 events = sel.select(timeout=1)
# # #                 for key, _ in events:
# # #                     line = key.fileobj.readline()
# # #                     if not line:
# # #                         continue
# # #                     line = line.rstrip()
# # #                     if key.fileobj is proc.stdout:
# # #                         if line:
# # #                             JOBS[job_id]["progress"] = line[:160]
# # #                     else:
# # #                         if line:
# # #                             stderr_lines.append(line)
# # #                             JOBS[job_id]["progress"] = f"[err] {line[:150]}"
# # #                 if proc.poll() is not None:
# # #                     # drain remaining output
# # #                     for line in proc.stdout:
# # #                         line = line.rstrip()
# # #                         if line:
# # #                             JOBS[job_id]["progress"] = line[:160]
# # #                     for line in proc.stderr:
# # #                         line = line.rstrip()
# # #                         if line:
# # #                             stderr_lines.append(line)
# # #                     break
# # #             sel.close()
# # #             if proc.returncode == 0:
# # #                 on_done(job_id)
# # #             else:
# # #                 err_detail = "\n".join(stderr_lines[-20:]) if stderr_lines else ""
# # #                 JOBS[job_id]["status"] = "failed"
# # #                 JOBS[job_id]["error"] = (
# # #                     f"Process exited with code {proc.returncode}"
# # #                     + (f"\n{err_detail}" if err_detail else "")
# # #                 )
# # #         except Exception as exc:
# # #             JOBS[job_id]["status"] = "failed"
# # #             JOBS[job_id]["error"] = str(exc)

# # #     threading.Thread(target=_worker, daemon=True).start()


# # # # ── Static / root ──────────────────────────────────────────────────────────────

# # # @app.get("/")
# # # async def serve_ui():
# # #     return FileResponse(UI_DIR / "index.html")


# # # @app.get("/graph")
# # # async def serve_graph():
# # #     return FileResponse(UI_DIR / "graph.html")


# # # # ── Graph list ─────────────────────────────────────────────────────────────────

# # # @app.get("/api/graphs")
# # # async def list_graphs():
# # #     """List all .graphml files with basic stats."""
# # #     result = []

# # #     for graph_id, spec in BUILTIN_GRAPHS.items():
# # #         _, builtin_path = _find_builtin_graph(graph_id)
# # #         if builtin_path is None:
# # #             continue
# # #         nodes, edges = _load_graph_data(graph_id)
# # #         result.append({
# # #             "filename": graph_id,
# # #             "label": spec["label"],
# # #             "domain": spec["domain"],
# # #             "modified": builtin_path.stat().st_mtime,
# # #             "nodes": len(nodes),
# # #             "edges": len(edges),
# # #             "is_builtin": True,
# # #         })

# # #     files = []
# # #     if GRAPHS_DIR.exists():
# # #         files = sorted(
# # #             GRAPHS_DIR.glob("*.graphml"),
# # #             key=lambda f: f.stat().st_mtime,
# # #             reverse=True,
# # #         )

# # #     for f in files:
# # #         nodes, edges = _load_graph_data(f.name)
# # #         result.append({
# # #             "filename": f.name,
# # #             "label": f.name,
# # #             "domain": None,
# # #             "modified": f.stat().st_mtime,
# # #             "nodes": len(nodes),
# # #             "edges": len(edges),
# # #             "is_builtin": False,
# # #         })

# # #     result.sort(key=lambda g: g["modified"], reverse=True)
# # #     return {"graphs": result}


# # # # ── Graph data (Cytoscape elements) ───────────────────────────────────────────

# # # @app.get("/api/graphs/{filename}")
# # # async def get_graph(filename: str, limit: int = 400):
# # #     """
# # #     Return graph stats + Cytoscape elements.
# # #     Only the top `limit` nodes by degree are returned to keep rendering fast.
# # #     """
# # #     nodes, edges = _load_graph_data(filename)
# # #     if not nodes:
# # #         raise HTTPException(404, f"No data found for graph: {filename}")
# # #     _, graph_meta = _resolve_graph_path(filename)

# # #     # Degree from edges
# # #     degree: dict[str, int] = {}
# # #     for e in edges:
# # #         s, t = e.get("source"), e.get("target")
# # #         if s:
# # #             degree[s] = degree.get(s, 0) + 1
# # #         if t:
# # #             degree[t] = degree.get(t, 0) + 1

# # #     # Top-N nodes by degree
# # #     top_nodes = sorted(nodes, key=lambda n: degree.get(n["id"], 0), reverse=True)[:limit]
# # #     top_ids = {n["id"] for n in top_nodes}

# # #     # Edges whose both endpoints are in the shown set
# # #     top_edges = [
# # #         e for e in edges
# # #         if e.get("source") in top_ids and e.get("target") in top_ids
# # #     ]

# # #     # Full-graph type counts
# # #     type_counts = dict(Counter(n.get("type", "Other") for n in nodes))

# # #     cy_nodes = [
# # #         {
# # #             "data": {
# # #                 "id": n["id"],
# # #                 "label": n.get("label", n["id"])[:50],
# # #                 "type": n.get("type", "Other"),
# # #                 "degree": degree.get(n["id"], 0),
# # #             }
# # #         }
# # #         for n in top_nodes
# # #     ]
# # #     cy_edges = [
# # #         {
# # #             "data": {
# # #                 "source": e["source"],
# # #                 "target": e["target"],
# # #                 "label": e.get("rnorm", e.get("title", "related_to")),
# # #                 "conf": float(e.get("weight", 0.7)),
# # #             }
# # #         }
# # #         for e in top_edges
# # #     ]

# # #     return {
# # #         "graph": graph_meta,
# # #         "stats": {
# # #             "nodes": len(nodes),
# # #             "edges": len(edges),
# # #             "shown_nodes": len(top_nodes),
# # #             "shown_edges": len(top_edges),
# # #             "node_types": type_counts,
# # #         },
# # #         "elements": cy_nodes + cy_edges,
# # #     }


# # # # ── Upload graphml ────────────────────────────────────────────────────────────

# # # @app.post("/api/graphs/upload")
# # # async def upload_graph(file: UploadFile = File(...)):
# # #     if not file.filename.endswith(".graphml"):
# # #         raise HTTPException(400, "Only .graphml files are accepted.")
# # #     GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
# # #     dest = GRAPHS_DIR / file.filename
# # #     dest.write_bytes(await file.read())
# # #     return {"filename": file.filename}


# # # # ── Build KG ───────────────────────────────────────────────────────────────────

# # # @app.post("/api/graphs/build")
# # # async def build_graph(req: BuildKGRequest):
# # #     """Start a build_kg.py run in the background and return a job ID."""
# # #     job_id = _make_job("Preparing build…")

# # #     cmd = [
# # #         sys.executable, str(ROOT / "build_kg.py"),
# # #         "--max-per-query", str(req.max_papers),
# # #     ]
# # #     if not req.use_s2:
# # #         cmd.append("--no-s2")
# # #     if not req.use_clinicaltrials:
# # #         cmd.append("--no-clinicaltrials")
# # #     if req.source_weights:
# # #         cmd += ["--source-weights", json.dumps(req.source_weights)]
# # #     if req.query_file:
# # #         query_path = QUERIES_UPLOAD_DIR / req.query_file
# # #         if query_path.exists():
# # #             cmd += ["--queries", str(query_path)]
# # #         else:
# # #             raise HTTPException(404, f"Uploaded query file not found: {req.query_file}")

# # #     def on_done(jid):
# # #         files = sorted(
# # #             GRAPHS_DIR.glob("*.graphml"),
# # #             key=lambda f: f.stat().st_mtime,
# # #             reverse=True,
# # #         )
# # #         JOBS[jid]["status"] = "done"
# # #         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# # #     _run_subprocess(cmd, job_id, on_done)
# # #     return {"job_id": job_id}


# # # # ── Upload a search-query file (used by the Create Graph form) ────────────────

# # # @app.post("/api/queries/upload")
# # # async def upload_queries(file: UploadFile = File(...)):
# # #     if not (file.filename.endswith(".json") or file.filename.endswith(".txt")):
# # #         raise HTTPException(400, "Only .json or .txt query files are accepted.")
# # #     QUERIES_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
# # #     dest = QUERIES_UPLOAD_DIR / file.filename
# # #     content = await file.read()
# # #     if file.filename.endswith(".json"):
# # #         try:
# # #             json.loads(content)
# # #         except Exception:
# # #             raise HTTPException(400, "That .json file isn't valid JSON.")
# # #     dest.write_bytes(content)
# # #     return {"filename": file.filename}


# # # # ── Enhance KG ─────────────────────────────────────────────────────────────────

# # # @app.post("/api/graphs/enhance")
# # # async def enhance_graph(req: EnhanceRequest):
# # #     """
# # #     Enhance an existing graph with new sources.
# # #     Currently supports keyword-based expansion (re-runs build_kg with new queries).
# # #     URL/file ingestion is a planned feature.
# # #     """
# # #     if req.enhance_type == "url":
# # #         if not req.url:
# # #             raise HTTPException(400, "Provide a url.")

# # #         nct_match = NCT_ID_RE.search(req.url)
# # #         if not nct_match:
# # #             # Only clinicaltrials.gov single-trial URLs are wired up today.
# # #             # Anything else (a generic paper URL, a news article, etc.) still
# # #             # needs real fetch/parse support that doesn't exist yet.
# # #             raise HTTPException(
# # #                 501,
# # #                 "URL ingestion currently only supports ClinicalTrials.gov trial "
# # #                 "links (containing an NCT ID, e.g. .../study/NCT01234567). "
# # #                 "General URL ingestion is not yet implemented.",
# # #             )

# # #         graph_path, _ = _resolve_graph_path(req.graph_filename)
# # #         job_id = _make_job(f"Fetching trial {nct_match.group(0)}…")

# # #         cmd = [
# # #             sys.executable, str(ROOT / "build_kg.py"),
# # #             "--nct-id", nct_match.group(0),
# # #             "--base-graph", str(graph_path),
# # #         ]

# # #         def on_done_url(jid):
# # #             files = sorted(
# # #                 GRAPHS_DIR.glob("*.graphml"),
# # #                 key=lambda f: f.stat().st_mtime,
# # #                 reverse=True,
# # #             )
# # #             JOBS[jid]["status"] = "done"
# # #             JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# # #         _run_subprocess(cmd, job_id, on_done_url)
# # #         return {"job_id": job_id}

# # #     if req.enhance_type != "keywords" or not req.keywords:
# # #         raise HTTPException(400, "Provide enhance_type='keywords' and a non-empty keywords list.")

# # #     job_id = _make_job("Running targeted literature search…")

# # #     cmd = [
# # #         sys.executable, str(ROOT / "build_kg.py"),
# # #         "--max-per-query", str(req.max_papers),
# # #         "--no-s2",
# # #     ]

# # #     def on_done(jid):
# # #         files = sorted(
# # #             GRAPHS_DIR.glob("*.graphml"),
# # #             key=lambda f: f.stat().st_mtime,
# # #             reverse=True,
# # #         )
# # #         JOBS[jid]["status"] = "done"
# # #         JOBS[jid]["result"] = {"filename": files[0].name if files else None}

# # #     _run_subprocess(cmd, job_id, on_done)
# # #     return {"job_id": job_id}


# # # # ── Hypothesis generation ──────────────────────────────────────────────────────

# # # @app.post("/api/hypothesis")
# # # async def generate_hypothesis(req: HypothesisRequest):
# # #     """Run the full SciAgents hypothesis pipeline and return a job ID."""
# # #     graph_path, _ = _resolve_graph_path(req.graph_filename)

# # #     job_id = _make_job("Initialising agents…")

# # #     cmd = [
# # #         sys.executable, str(ROOT / "run_pipeline.py"),
# # #         "--graph", str(graph_path),
# # #         "--query", req.query,
# # #     ]

# # #     def on_done(jid):
# # #         # Find the most recently written hypothesis .md
# # #         md_files = sorted(
# # #             list(GRAPHS_DIR.glob("hypothesis_run*.md"))
# # #             + list(OUTPUTS_DIR.rglob("hypothesis_run*.md")),
# # #             key=lambda f: f.stat().st_mtime,
# # #             reverse=True,
# # #         )
# # #         hypothesis_text = md_files[0].read_text(encoding="utf-8") if md_files else ""
# # #         JOBS[jid]["status"] = "done"
# # #         JOBS[jid]["result"] = {"hypothesis": hypothesis_text[:8000]}

# # #     _run_subprocess(cmd, job_id, on_done)
# # #     return {"job_id": job_id}


# # # # ── Focused path discovery ─────────────────────────────────────────────────────

# # # @app.post("/api/path-discovery")
# # # async def run_path_discovery(req: PathDiscoveryRequest):
# # #     """Run only graph-path discovery/ranking and return a job ID."""
# # #     graph_path, _ = _resolve_graph_path(req.graph_filename)

# # #     job_id = _make_job("Preparing path discovery…")
# # #     PATH_DISCOVERY_DIR.mkdir(parents=True, exist_ok=True)
# # #     out_txt = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.txt"
# # #     out_json = PATH_DISCOVERY_DIR / f"ui_path_discovery_{job_id}.json"

# # #     cmd = [
# # #         sys.executable, str(ROOT / "run_path_discovery.py"),
# # #         "--graph", str(graph_path),
# # #         "--query", req.query,
# # #         "--source", req.source,
# # #         "--target-type", req.target_type,
# # #         "--domain", req.domain,
# # #         "--max-targets", str(req.max_targets),
# # #         "--top-n", str(req.top_n),
# # #         "--seed", str(req.seed),
# # #         "--logic-weight", str(req.logic_weight),
# # #         "--relevance-weight", str(req.relevance_weight),
# # #         "--novelty-weight", str(req.novelty_weight),
# # #         "--surprise-weight", str(req.surprise_weight),
# # #         "--rank-by", req.rank_by,
# # #         "--out", str(out_txt),
# # #         "--json-out", str(out_json),
# # #     ]
# # #     if req.skip_logic:
# # #         cmd.append("--skip-logic")

# # #     def on_done(jid):
# # #         result = {
# # #             "text": out_txt.read_text(encoding="utf-8") if out_txt.exists() else "",
# # #             "text_path": str(out_txt.relative_to(ROOT)) if out_txt.exists() else None,
# # #             "json_path": str(out_json.relative_to(ROOT)) if out_json.exists() else None,
# # #         }
# # #         if out_json.exists():
# # #             try:
# # #                 result["data"] = json.loads(out_json.read_text(encoding="utf-8"))
# # #             except Exception:
# # #                 result["data"] = None
# # #         JOBS[jid]["status"] = "done"
# # #         JOBS[jid]["result"] = result

# # #     _run_subprocess(cmd, job_id, on_done)
# # #     return {"job_id": job_id}


# # # # ── Job status ─────────────────────────────────────────────────────────────────

# # # @app.get("/api/jobs/{job_id}")
# # # async def get_job_status(job_id: str):
# # #     job = JOBS.get(job_id)
# # #     if not job:
# # #         raise HTTPException(404, "Job not found")
# # #     return job


# # # # ── Entry point ────────────────────────────────────────────────────────────────

# # # if __name__ == "__main__":
# # #     print(f"\n  SAGE UI  →  http://localhost:8000\n")
# # #     uvicorn.run(
# # #         "server:app",
# # #         host="0.0.0.0",
# # #         port=8000,
# # #         reload=True,
# # #         reload_dirs=[str(UI_DIR)],
# # #         app_dir=str(UI_DIR),
# # #     )

