"""
Wrapper around Paperclip (gxl.ai) — SDK-first (API key), CLI-subprocess fallback.

Setup (preferred — API key, no browser/OAuth needed, works on headless servers):
    pip install gxl_paperclip
    export PAPERCLIP_API_KEY=pk_...      # or put PAPERCLIP_API_KEY=pk_... in .env

Fallback (CLI, requires an interactive OAuth sign-in — awkward on a remote
server, kept only so this still works if no API key is configured):
    curl -fsSL https://paperclip.gxl.ai/install.sh | bash
    paperclip login

Why SDK-first: the CLI has no JSON output mode (confirmed against the real
CLI — see git history of this file), so the original implementation had to
parse plain text scraped from a subprocess. The SDK exposes the *same*
plain-text via `result.output` (per Paperclip's own docs: "same formatted
text the CLI prints"), so the already-verified text parser below is reused
unchanged — only the acquisition method changes (direct Python call with API
key auth, instead of subprocess + CLI + PATH + interactive OAuth).

Confirmed valid `source` values (from the CLI's own usage error message):
    pmc, biorxiv, medrxiv, arxiv, abstracts,
    fda, fda/jp, fda/eu,
    trials, trials/us, trials/eu, trials/jp, trials/cn,
    proteins, uniprot (alias for proteins)
Note: "patents" is NOT a valid source, despite earlier (incorrect) docs.

UNVERIFIED: lit_grep() / lit_map() syntax, for both SDK and CLI paths — no
real sample output was available for either at time of writing. Both fail
soft (log + return empty) rather than raise, so a wrong guess here degrades
gracefully — paperclip_extractor.py already falls back to paper abstracts
when lit_grep returns nothing.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("paperclip_tools")

VALID_SOURCES = {
    "pmc", "biorxiv", "medrxiv", "arxiv", "abstracts",
    "fda", "fda/jp", "fda/eu",
    "trials", "trials/us", "trials/eu", "trials/jp", "trials/cn",
    "proteins", "uniprot",
}

_HEADER_RE = re.compile(r"^Found\s+(\d+)\s+papers?\s+\[(\S+)\]", re.IGNORECASE)
_ENTRY_START_RE = re.compile(r"^(\d+)\.\s+(.*)$")


# ---------------------------------------------------------------------------
# Shared text parser (proven against real CLI output — do not change without
# re-testing against a real sample; see conversation history for the sample
# used to verify this)
# ---------------------------------------------------------------------------

def _parse_search_output(text: str) -> Tuple[str, List[Dict]]:
    lines = text.splitlines()
    result_id = ""
    body_start = 0
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line.strip())
        if m:
            result_id = m.group(2)
            body_start = i + 1
            break

    entries: List[List[str]] = []
    current: List[str] = []
    for line in lines[body_start:]:
        m = _ENTRY_START_RE.match(line)
        if m:
            if current:
                entries.append(current)
            current = [m.group(2)]
        elif current:
            if line.strip():
                current.append(line.strip())
    if current:
        entries.append(current)

    papers: List[Dict] = []
    for entry in entries:
        title = entry[0] if len(entry) > 0 else ""
        authors = entry[1] if len(entry) > 1 else ""
        meta_line = entry[2] if len(entry) > 2 else ""
        url = entry[3] if len(entry) > 3 else ""
        snippet_raw = entry[4] if len(entry) > 4 else ""
        snippet = snippet_raw.strip('"')

        pid, journal, year = "", "", ""
        parts = [p.strip() for p in meta_line.split("·")]
        if len(parts) >= 1:
            pid = parts[0]
        if len(parts) >= 2:
            journal = parts[1]
        if len(parts) >= 3:
            year = parts[2][:4] if parts[2][:4].isdigit() else parts[2]

        papers.append({
            "pmid": pid,
            "id": pid,
            "title": title,
            "abstract": snippet,
            "snippet": snippet,
            "year": year,
            "journal": journal,
            "url": url,
            "doi": "",
            "citationCount": 0,
            "authors": authors,
        })

    if not papers:
        log.warning("lit_search: parsed 0 papers from Paperclip output — "
                     "output format may have changed. Raw output:\n%s", text[:1000])

    return result_id, papers


# ---------------------------------------------------------------------------
# SDK path (preferred)
# ---------------------------------------------------------------------------

def _sdk_client():
    """Return a gxl_paperclip PaperclipClient, or None if the SDK isn't
    installed / no API key is configured. Import is lazy so this module
    still works (via the CLI fallback) without the SDK installed."""
    if not os.environ.get("PAPERCLIP_API_KEY"):
        return None
    try:
        from gxl_paperclip import PaperclipClient
    except ImportError:
        log.warning("PAPERCLIP_API_KEY is set but gxl_paperclip isn't installed "
                     "(pip install gxl_paperclip) — falling back to the CLI.")
        return None
    try:
        return PaperclipClient.from_env()
    except Exception as exc:
        log.warning("Failed to initialize PaperclipClient from PAPERCLIP_API_KEY "
                     "(falling back to CLI): %s", exc)
        return None


def _sdk_search(query: str, source: str, n: int,
                since: Optional[str], date_end: Optional[str]) -> Tuple[str, List[Dict]]:
    client = _sdk_client()
    if client is None:
        raise RuntimeError("SDK client unavailable")  # caller falls back to CLI

    kwargs = {"limit": n, "source": source}
    # since/until kwarg names on client.search() are unverified — try with
    # them, retry without if the SDK rejects the kwargs, rather than
    # crashing on a wrong guess.
    if since:
        kwargs["since"] = since
    if date_end:
        kwargs["until"] = date_end
    try:
        result = client.search(query, **kwargs)
    except TypeError:
        kwargs.pop("since", None)
        kwargs.pop("until", None)
        result = client.search(query, **kwargs)

    output_text = getattr(result, "output", None)
    if output_text is None:
        output_text = str(result)
    result_id = getattr(result, "result_id", "") or ""

    _, papers = _parse_search_output(output_text)
    # Prefer the SDK's own result_id attribute over anything parsed from text
    result_id = result_id or _parse_search_output(output_text)[0]
    return result_id, papers


# ---------------------------------------------------------------------------
# CLI path (fallback — used when no PAPERCLIP_API_KEY / gxl_paperclip)
# ---------------------------------------------------------------------------

def _cli() -> str:
    path = shutil.which("paperclip")
    if path is None:
        raise RuntimeError(
            "Paperclip is not usable: no PAPERCLIP_API_KEY set (preferred — "
            "pip install gxl_paperclip, then export PAPERCLIP_API_KEY=pk_...) "
            "and the `paperclip` CLI isn't on PATH either.\n"
            "CLI fallback install: curl -fsSL https://paperclip.gxl.ai/install.sh | bash\n"
            "then `paperclip login`."
        )
    return path


def _run_text(args: List[str]) -> str:
    cmd = [_cli()] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"paperclip command failed (exit {exc.returncode}):\n"
            f"  cmd : {' '.join(cmd)}\n"
            f"  stderr: {exc.stderr.strip()}\n"
            f"  stdout: {exc.stdout.strip()[:400]}"
        ) from exc
    return proc.stdout


def _cli_search(query: str, source: str, n: int,
                since: Optional[str], date_end: Optional[str]) -> Tuple[str, List[Dict]]:
    args = ["search", "-s", source, query, "-n", str(n)]
    if since:
        args += ["--since", since]
    if date_end:
        args += ["--until", date_end]
    text = _run_text(args)
    return _parse_search_output(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def lit_search(
    query: str,
    source: str = "pmc",
    n: int = 10,
    since: Optional[str] = None,
    date_end: Optional[str] = None,
) -> Tuple[str, List[Dict]]:
    """Search the Paperclip corpus. Tries the API-key SDK first (if
    PAPERCLIP_API_KEY is set and gxl_paperclip is installed), falls back to
    the CLI subprocess otherwise.

    Returns (result_id, papers) — see module docstring for paper dict shape.
    """
    if source not in VALID_SOURCES:
        log.warning("Unrecognized Paperclip source %r (not in %s) — passing through anyway.",
                     source, sorted(VALID_SOURCES))

    if os.environ.get("PAPERCLIP_API_KEY"):
        try:
            return _sdk_search(query, source, n, since, date_end)
        except Exception as exc:
            log.warning("SDK search failed, falling back to CLI: %s", exc)

    return _cli_search(query, source, n, since, date_end)


def lit_grep(result_id: str, pattern: str) -> List[Dict]:
    """Regex-search within a Paperclip result set. UNVERIFIED syntax — fails
    soft (empty list) so paperclip_extractor.py's abstract fallback kicks in
    instead of crashing the pipeline."""
    client = _sdk_client()
    if client is not None:
        try:
            grep_fn = getattr(client, "grep", None) or getattr(client, "grep_", None)
            if grep_fn:
                result = grep_fn(pattern, from_results=result_id)
                text = getattr(result, "output", None) or str(result)
                return [{"text": line.strip()} for line in text.splitlines() if line.strip()]
        except Exception as exc:
            log.warning("SDK lit_grep failed (falling back to CLI): %s", exc)

    try:
        text = _run_text(["grep", result_id, pattern])
    except Exception as exc:
        log.warning("lit_grep failed (falling back to abstracts): %s", exc)
        return []
    return [{"text": line.strip()} for line in text.splitlines() if line.strip()]


def lit_map(
    result_id: str,
    question: str,
    output_schema: Optional[Dict] = None,
) -> List[Dict]:
    """Structured extraction over a Paperclip result set (opt-in enrichment).
    UNVERIFIED syntax for both SDK and CLI paths. Already opt-in
    (expand_with_map=False by default) and already wrapped in try/except at
    its call site in paperclip_extractor.py — safe to fail here."""
    client = _sdk_client()
    if client is not None:
        try:
            map_fn = getattr(client, "map_", None) or getattr(client, "map", None)
            if map_fn:
                events = list(map_fn(question, from_results=result_id))
                answer = " ".join(
                    str(getattr(e, "text", e)) for e in events
                    if getattr(e, "type", "") != "progress"
                )
                return [{"paper_id": "", "answer": answer}] if answer.strip() else []
        except Exception as exc:
            log.warning("SDK lit_map failed (falling back to CLI): %s", exc)

    try:
        text = _run_text(["map", result_id, question])
    except Exception as exc:
        log.warning("lit_map failed: %s", exc)
        return []
    return [{"paper_id": "", "answer": text.strip()}] if text.strip() else []