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
import subprocess
import sys
import threading
import uuid
from collections import Counter
from pathlib import Path
from typing import List, Optional

import networkx as nx
import uvicorn
from fastapi import FastAPI, Form, HTTPException, UploadFile, File
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
    max_papers: int = 20


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
        "stdout_lines": [],
        "result": None,
        "error": None,
    }
    return job_id


def _run_subprocess(cmd: list[str], job_id: str, on_done) -> None:
    """Run a subprocess in a daemon thread, streaming stdout to job progress."""
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
                            JOBS[job_id]["progress"] = line[:160]
                            JOBS[job_id]["stdout_lines"].append(line)
                    else:
                        if line:
                            stderr_lines.append(line)
                            JOBS[job_id]["progress"] = f"[err] {line[:150]}"
                if proc.poll() is not None:
                    # drain remaining output
                    for line in proc.stdout:
                        line = line.rstrip()
                        if line:
                            JOBS[job_id]["progress"] = line[:160]
                            JOBS[job_id]["stdout_lines"].append(line)
                    for line in proc.stderr:
                        line = line.rstrip()
                        if line:
                            stderr_lines.append(line)
                    break
            sel.close()
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


# ── Upload graphml ────────────────────────────────────────────────────────────

@app.post("/api/graphs/upload")
async def upload_graph(file: UploadFile = File(...)):
    if not file.filename.endswith(".graphml"):
        raise HTTPException(400, "Only .graphml files are accepted.")
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    dest = GRAPHS_DIR / file.filename
    dest.write_bytes(await file.read())
    return {"filename": file.filename}


# ── Build KG ───────────────────────────────────────────────────────────────────

@app.post("/api/graphs/build")
async def build_graph(req: BuildKGRequest):
    """Start a build_kg.py run in the background and return a job ID."""
    job_id = _make_job("Preparing build…")

    cmd = [
        sys.executable, str(ROOT / "build_kg.py"),
        "--max-per-query", str(req.max_papers),
    ]
    if not req.use_s2:
        cmd.append("--no-s2")

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


# ── Enhance KG ─────────────────────────────────────────────────────────────────

def _ingest_on_done(jid: str) -> None:
    """Parse the OUTPUT_GRAPHML= line from ingest_source.py stdout to find the new file."""
    new_name = None
    for line in JOBS[jid].get("stdout_lines", []):
        if line.startswith("OUTPUT_GRAPHML="):
            new_name = Path(line.split("=", 1)[1].strip()).name
            break
    if new_name is None:
        # Fallback: newest graphml in GRAPHS_DIR
        files = sorted(GRAPHS_DIR.glob("*.graphml"), key=lambda f: f.stat().st_mtime, reverse=True)
        new_name = files[0].name if files else None
    JOBS[jid]["status"] = "done"
    JOBS[jid]["result"] = {"filename": new_name}


@app.post("/api/graphs/enhance-source")
async def enhance_graph_source(
    graph_filename: str = Form(...),
    url: str = Form(None),
    file: UploadFile = File(None),
    paperclip_query: str = Form(None),
    paperclip_source: str = Form("pmc"),
    paperclip_n: int = Form(10),
):
    """Ingest a document file, URL, or Paperclip literature search into an existing graph."""
    if not graph_filename:
        raise HTTPException(400, "graph_filename is required.")
    has_file = bool(file and file.filename)
    if not url and not paperclip_query and not has_file:
        raise HTTPException(400, "Provide a file, a URL, or a Paperclip search query.")

    graph_path, _ = _resolve_graph_path(graph_filename)

    # Paperclip literature search — routed to its own script since it needs
    # the Paperclip CLI (gxl.ai) and different search parameters (corpus, n).
    if paperclip_query and not url and not has_file:
        job_id = _make_job(f"Searching Paperclip ({paperclip_source})…")
        cmd = [
            sys.executable, str(ROOT / "paperclip_ingest.py"),
            "--graph", str(graph_path),
            "--query", paperclip_query,
            "--source", paperclip_source,
            "--n", str(paperclip_n),
            "--out-dir", str(GRAPHS_DIR),
        ]
        _run_subprocess(cmd, job_id, _ingest_on_done)
        return {"job_id": job_id}

    # Save uploaded file to a temp location the subprocess can read
    tmp_file: Path | None = None
    if file and file.filename:
        import tempfile
        suffix = Path(file.filename).suffix or ".bin"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(await file.read())
        tmp.close()
        tmp_file = Path(tmp.name)

    job_id = _make_job("Extracting text and triples…")

    cmd = [
        sys.executable, str(ROOT / "ingest_source.py"),
        "--graph", str(graph_path),
        "--out-dir", str(GRAPHS_DIR),
    ]
    if tmp_file:
        cmd += ["--file", str(tmp_file)]
    elif url:
        cmd += ["--url", url]

    def on_done(jid):
        if tmp_file and tmp_file.exists():
            try:
                tmp_file.unlink()
            except Exception:
                pass
        _ingest_on_done(jid)

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


@app.post("/api/graphs/build-paperclip")
async def build_graph_paperclip(
    query: str = Form(...),
    source: str = Form("pmc"),
    n: int = Form(15),
    graph_root: str = Form(""),
):
    """Start a brand-new graph from a Paperclip literature search (no existing
    graph required — paperclip_ingest.py saves it fresh when --graph is omitted)."""
    if not query:
        raise HTTPException(400, "query is required.")

    job_id = _make_job(f"Searching Paperclip ({source})…")
    cmd = [
        sys.executable, str(ROOT / "paperclip_ingest.py"),
        "--query", query,
        "--source", source,
        "--n", str(n),
        "--out-dir", str(GRAPHS_DIR),
    ]
    if graph_root:
        cmd += ["--graph-root", graph_root]

    _run_subprocess(cmd, job_id, _ingest_on_done)
    return {"job_id": job_id}


@app.post("/api/graphs/enhance")
async def enhance_graph(req: EnhanceRequest):
    """Enhance an existing graph via keyword-based literature search or URL ingestion."""
    if req.enhance_type == "url":
        if not req.url:
            raise HTTPException(400, "Provide a url when enhance_type='url'.")
        graph_path, _ = _resolve_graph_path(req.graph_filename)
        job_id = _make_job("Fetching and ingesting source…")
        cmd = [
            sys.executable, str(ROOT / "ingest_source.py"),
            "--graph", str(graph_path),
            "--url", req.url,
            "--out-dir", str(GRAPHS_DIR),
        ]
        def on_done_url(jid):
            _ingest_on_done(jid)
        _run_subprocess(cmd, job_id, on_done_url)
        return {"job_id": job_id}

    if req.enhance_type != "keywords" or not req.keywords:
        raise HTTPException(400, "Provide enhance_type='keywords' and a non-empty keywords list.")

    job_id = _make_job("Running targeted literature search…")

    cmd = [
        sys.executable, str(ROOT / "build_kg.py"),
        "--max-per-query", str(req.max_papers),
        "--no-s2",
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
        # Find the most recently written hypothesis .md AND .txt — .txt is
        # the full raw agent-by-agent transcript (what the CLI itself saves
        # as hypothesis_run_<timestamp>.txt); .md is a shorter, polished
        # report. The in-page preview stays truncated for readable display,
        # but download buttons now serve the real, full, untruncated files
        # straight from disk via /api/hypothesis-file/, not this preview text.
        md_files = sorted(
            list(GRAPHS_DIR.glob("hypothesis_run*.md"))
            + list(OUTPUTS_DIR.rglob("hypothesis_run*.md")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        txt_files = sorted(
            list(GRAPHS_DIR.glob("hypothesis_run*.txt"))
            + list(OUTPUTS_DIR.rglob("hypothesis_run*.txt")),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        md_path = md_files[0] if md_files else None
        txt_path = txt_files[0] if txt_files else None

        preview_text = md_path.read_text(encoding="utf-8") if md_path else ""
        JOBS[jid]["status"] = "done"
        JOBS[jid]["result"] = {
            "hypothesis": preview_text[:8000],
            "md_path": str(md_path) if md_path else None,
            "txt_path": str(txt_path) if txt_path else None,
        }

    _run_subprocess(cmd, job_id, on_done)
    return {"job_id": job_id}


@app.get("/api/hypothesis-file/{job_id}/{ext}")
async def download_hypothesis_file(job_id: str, ext: str):
    """Serve the real, full hypothesis file (.txt or .md) for a completed
    job, straight from disk — no truncation, unlike the in-page preview."""
    if ext not in ("txt", "md"):
        raise HTTPException(400, "ext must be 'txt' or 'md'")
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    result = job.get("result") or {}
    path_str = result.get(f"{ext}_path")
    if not path_str:
        raise HTTPException(404, f"No .{ext} file was recorded for this job.")
    file_path = Path(path_str)
    if not file_path.exists():
        raise HTTPException(404, f"File no longer exists on disk: {file_path}")
    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="text/plain" if ext == "txt" else "text/markdown",
    )


# ── Output files browser (everything saved under outputs/ and path_discovery/) ─

# Directories exposed for browsing/download. Kept as an explicit allow-list
# so this can never be tricked into serving files outside these trees.
_BROWSABLE_DIRS = {
    "outputs": OUTPUTS_DIR,
    "path_discovery": PATH_DISCOVERY_DIR,
}


def _resolve_browsable_path(root_key: str, rel_path: str) -> Path:
    """Resolve rel_path under one of _BROWSABLE_DIRS, rejecting any attempt
    to escape that directory (path traversal)."""
    base = _BROWSABLE_DIRS.get(root_key)
    if base is None:
        raise HTTPException(400, f"Unknown root '{root_key}'. Valid roots: {list(_BROWSABLE_DIRS)}")
    candidate = (base / rel_path).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path.")
    return candidate


@app.get("/api/output-files")
async def list_output_files():
    """List every file under outputs/ and path_discovery/, for a generic
    'everything we've saved' browser in the UI — not just graphs and
    hypotheses, but node/edge JSON sidecars, path-discovery results,
    error reports, build logs, etc."""
    files = []
    for root_key, base in _BROWSABLE_DIRS.items():
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.is_file():
                files.append({
                    "root": root_key,
                    "rel_path": str(f.relative_to(base)),
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@app.get("/api/output-files/download")
async def download_output_file(root: str, path: str):
    file_path = _resolve_browsable_path(root, path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, f"File not found: {path}")
    return FileResponse(path=str(file_path), filename=file_path.name)


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