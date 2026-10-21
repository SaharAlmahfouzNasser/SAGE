"""
Report Generator Module

Converts raw agent pipeline output (.txt) into a clinician-friendly
Markdown report (.md) and PDF (.pdf).

The report follows a 5-section structure designed for presentation
to oncologists / clinical collaborators:

  1. Clinical Question & Core Hypothesis
  2. Why This Gene (not others)
  3. Clinically Testable Predictions
  4. Minimum Viable Validation Plan
  5. Strengths, Caveats & Alternative Directions
"""

import argparse
import json
import os
import re
from typing import Any

import markdown
from xhtml2pdf import pisa


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def read_text_file(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Parsing: extract per-agent outputs from the raw console log
# ---------------------------------------------------------------------------

def parse_agent_outputs(content: str) -> dict[str, Any]:
    """Split the raw console log by ``---------- `` delimiters and return
    the *last* output of each named agent.

    For most agents the output is JSON-parsed; ``hypothesis_expansion_agent``
    is kept as a raw string first and then parsed separately because it may
    contain escaped characters.
    """
    sections = re.split(r"^---------- ", content, flags=re.MULTILINE)
    agent_outputs: dict[str, list[str]] = {}
    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().split("\n")
        first_line = lines[0]
        if "TextMessage" in first_line or "ToolCallSummaryMessage" in first_line:
            agent = first_line.split("(")[1].split(")")[0]
            output = "\n".join(lines[1:]).strip()
            agent_outputs.setdefault(agent, []).append(output)

    final_outputs: dict[str, Any] = {}
    for agent, outputs in agent_outputs.items():
        last_output = outputs[-1]
        # Try JSON parse for every agent (including hypothesis_expansion_agent)
        try:
            parsed = json.loads(last_output)
            final_outputs[agent] = parsed
        except json.JSONDecodeError:
            # Fallback: try to extract JSON object from the text
            json_match = re.search(r"\{.*\}", last_output, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    final_outputs[agent] = parsed
                except json.JSONDecodeError:
                    final_outputs[agent] = last_output
            else:
                final_outputs[agent] = last_output
    return final_outputs


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _get(d: Any, *keys: str, default: str = "") -> str:
    """Safely traverse nested dicts / extract string values."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, default)
        else:
            return default
    return str(d) if d else default


def _extract_hypothesis(agent_dict: dict) -> str:
    """Return the *final* hypothesis sentence (from the last scientist output)."""
    sci = agent_dict.get("scientist", {})
    if isinstance(sci, dict):
        return sci.get("hypothesis", "")
    return ""


def _extract_expanded_hypothesis(agent_dict: dict) -> str:
    exp = agent_dict.get("hypothesis_expansion_agent", {})
    if isinstance(exp, dict):
        return exp.get("expanded_hypothesis", "")
    if isinstance(exp, str):
        m = re.search(r'"expanded_hypothesis"\s*:\s*"((?:[^"\\]|\\.)*)"', exp, re.DOTALL)
        return m.group(1).replace("\\n", "\n") if m else ""
    return ""


def _extract_justification(agent_dict: dict) -> str:
    exp = agent_dict.get("hypothesis_expansion_agent", {})
    if isinstance(exp, dict):
        return exp.get("justification", "")
    if isinstance(exp, str):
        m = re.search(r'"justification"\s*:\s*"((?:[^"\\]|\\.)*)"', exp, re.DOTALL)
        return m.group(1).replace("\\n", "\n") if m else ""
    return ""


def _extract_core_variables(agent_dict: dict) -> dict:
    sci = agent_dict.get("scientist", {})
    if isinstance(sci, dict):
        return sci.get("core_variables", {})
    return {}


def _extract_secondary_modifiers(agent_dict: dict) -> list:
    sci = agent_dict.get("scientist", {})
    if isinstance(sci, dict):
        return sci.get("secondary_modifiers", [])
    return []


def _extract_scientist_notes(agent_dict: dict) -> str:
    sci = agent_dict.get("scientist", {})
    if isinstance(sci, dict):
        return sci.get("notes", "")
    return ""


def _extract_novelty_info(agent_dict: dict) -> dict:
    """Extract novelty score, verdict, rationale from multi_critic_coordinator."""
    critic = agent_dict.get("multi_critic_coordinator", "")
    if isinstance(critic, str):
        info: dict[str, str] = {}
        # Score
        m = re.search(r"Score:\s*(\d+/10)", critic)
        if m:
            info["score"] = m.group(1)
        # Verdict
        m = re.search(r"Verdict:\s*(\w+)", critic)
        if m:
            info["verdict"] = m.group(1)
        # Rationale
        m = re.search(r"RATIONALE\s*-+\s*\n(.*?)(?:\n-{10,}|\n={10,})", critic, re.DOTALL)
        if m:
            info["rationale"] = m.group(1).strip()
        return info
    return {}


def _extract_coding_tasks_summary(agent_dict: dict) -> list[dict]:
    """Return a list of simplified coding task dicts (task, description, clinical_relevance)."""
    coding = agent_dict.get("coding_agent_assistant", "")
    tasks: list[dict] = []
    # The coding agent output may be a string with ```json blocks
    raw = coding if isinstance(coding, str) else json.dumps(coding)
    for m in re.finditer(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL):
        try:
            t = json.loads(m.group(1))
            tasks.append(t)
        except json.JSONDecodeError:
            continue
    # If it was already a dict (single task), wrap it
    if not tasks and isinstance(coding, dict) and "task" in coding:
        tasks.append(coding)
    return tasks


def _extract_summary_agent(agent_dict: dict) -> dict:
    """Extract the summary agent JSON (if present)."""
    sa = agent_dict.get("summary_agent", {})
    if isinstance(sa, dict):
        return sa
    if isinstance(sa, str):
        m = re.search(r"\{.*\}", sa, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}


# ---------------------------------------------------------------------------
# Build the clinician-friendly Markdown
# ---------------------------------------------------------------------------

def build_clinician_report(agent_dict: dict) -> str:
    """Assemble a concise, 5-section Markdown report from parsed agent data."""

    hypothesis = _extract_hypothesis(agent_dict)
    expanded = _extract_expanded_hypothesis(agent_dict)
    justification = _extract_justification(agent_dict)
    core_vars = _extract_core_variables(agent_dict)
    secondary = _extract_secondary_modifiers(agent_dict)
    notes = _extract_scientist_notes(agent_dict)
    novelty = _extract_novelty_info(agent_dict)
    coding_tasks = _extract_coding_tasks_summary(agent_dict)
    summary = _extract_summary_agent(agent_dict)

    anchor_genes = core_vars.get("anchor_genes", [])
    imaging = core_vars.get("imaging_feature", "")
    endpoint = core_vars.get("endpoint", "")

    # Use summary agent data if available
    summary_obj = summary.get("comprehensive_summary", {})
    critical_eval = summary.get("critical_evaluation", {})
    novelty_scores = summary.get("novelty_and_feasibility_scores", {})
    future = summary.get("future_directions", {})
    references = summary.get("references", [])

    md = []

    # ======================================================================
    # SECTION 1 — Clinical Question & Core Hypothesis
    # ======================================================================
    md.append("# 1. Clinical Question & Core Hypothesis\n")

    # Derive a clinical question from notes / hypothesis
    clinical_q = _derive_clinical_question(notes, hypothesis)
    md.append(f"## Clinical Question\n\n{clinical_q}\n")

    md.append("## Working Hypothesis\n")
    # Prefer the expanded hypothesis; fall back to the raw one
    display_hyp = expanded if expanded else hypothesis
    if display_hyp:
        md.append(f"{display_hyp}\n")

    # Quick summary of core variables
    if anchor_genes or imaging or endpoint:
        md.append("\n| Element | Value |")
        md.append("| --- | --- |")
        if imaging:
            md.append(f"| Imaging feature | {imaging} |")
        if anchor_genes:
            md.append(f"| Anchor gene(s) | {', '.join(anchor_genes)} |")
        if endpoint:
            md.append(f"| Primary endpoint | {endpoint} |")
        md.append("")

    # ======================================================================
    # SECTION 2 — Why This Gene
    # ======================================================================
    md.append("\n# 2. Why This Gene — Not Others\n")

    if anchor_genes:
        md.append("## Primary hypothesis gene(s)\n")
        # Extract rationale from notes
        gene_rationale = _extract_gene_rationale(notes, anchor_genes)
        if gene_rationale:
            md.append(f"{gene_rationale}\n")

    if secondary:
        md.append("## Alternative / secondary genes considered\n")
        md.append("| Tier | Gene | Role / Reason excluded from core |")
        md.append("| --- | --- | --- |")
        secondary_info = _build_secondary_table(notes, secondary)
        for gene, reason in secondary_info:
            md.append(f"| Secondary | {gene} | {reason} |")
        md.append("")

    # ======================================================================
    # SECTION 3 — Clinically Testable Predictions
    # ======================================================================
    md.append("\n# 3. Clinically Testable Predictions\n")

    predictions = _extract_predictions(justification, hypothesis, expanded)
    for i, pred in enumerate(predictions, 1):
        md.append(f"**Prediction {i}:** {pred}\n")

    # ======================================================================
    # SECTION 4 — Minimum Viable Validation Plan
    # ======================================================================
    md.append("\n# 4. Minimum Viable Validation Plan\n")

    validation = _build_validation_plan(justification, coding_tasks)
    for tier_name, tier_content in validation:
        md.append(f"## {tier_name}\n")
        md.append(f"{tier_content}\n")

    # ======================================================================
    # SECTION 5 — Strengths, Caveats & Alternative Directions
    # ======================================================================
    md.append("\n# 5. Strengths, Caveats & Alternative Directions\n")

    md.append("## Strengths\n")
    strengths = _extract_strengths(justification, novelty, summary)
    for s in strengths:
        md.append(f"- {s}")
    md.append("")

    md.append("## Caveats & Risks\n")
    caveats = _extract_caveats(justification, critical_eval, novelty)
    for c in caveats:
        md.append(f"- {c}")
    md.append("")

    md.append("## Alternative Directions\n")
    alternatives = _extract_alternatives(secondary, notes, critical_eval)
    for a in alternatives:
        md.append(f"- {a}")
    md.append("")

    # Novelty / Feasibility one-liner
    score_line = _build_score_line(novelty, novelty_scores)
    if score_line:
        md.append(f"\n> **Assessment summary:** {score_line}\n")

    # References (if any)
    if references:
        md.append("\n---\n\n## References\n")
        for ref in references:
            md.append(f"- {ref}")
        md.append("")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# Content extraction / synthesis helpers
# ---------------------------------------------------------------------------

def _derive_clinical_question(notes: str, hypothesis: str) -> str:
    """Try to extract the 'central biological puzzle' from the scientist notes."""
    # Look for explicit puzzle phrasing
    m = re.search(r"Central biological puzzle:\s*(.*?)(?:\(\d\)|\n\n|$)", notes, re.IGNORECASE | re.DOTALL)
    if m:
        q = m.group(1).strip().rstrip(".?")
        # Clean up parenthetical numbers
        q = re.sub(r"\(\d+\)\s*", "", q)
        if q:
            return q + "?"

    # Fallback: derive from hypothesis
    if "only when" in hypothesis.lower() or "contingent" in hypothesis.lower():
        return "Why do some tumors with favorable immune architecture still fail to achieve better clinical outcomes?"
    return "What molecular mechanisms determine whether immune structures translate into clinical benefit?"


def _extract_gene_rationale(notes: str, anchor_genes: list) -> str:
    """Build a concise rationale for why the anchor gene(s) were chosen."""
    lines = []
    for gene in anchor_genes:
        # Try to find mentions in notes
        pattern = rf"{re.escape(gene)}[^.]*\."
        matches = re.findall(pattern, notes, re.IGNORECASE)
        if matches:
            # Take the most informative sentence
            best = max(matches, key=len)
            lines.append(f"**{gene}:** {best.strip()}")
        else:
            lines.append(f"**{gene}:** Selected as a core mechanistic driver from Tier 1 knowledge graph analysis.")
    return "\n\n".join(lines)


def _build_secondary_table(notes: str, secondary: list) -> list[tuple[str, str]]:
    """Return (gene, reason) pairs for secondary modifiers."""
    result = []
    known_roles = {
        "FAP": "Stromal exclusion / CAF barrier — excluded from core to focus on T-cell intrinsic mechanism",
        "ITGB1": "Integrin-mediated adhesion / ECM stiffness — relegated to modifier role",
        "ITGAV": "Integrin alpha-V subunit — paired with ITGB1; not core driver",
        "BIRC3": "Apoptosis resistance (IAP family) — tumor-intrinsic confounder",
        "PROCR": "Endothelial / vascular trafficking regulator",
        "FGFR2": "Epithelial-stromal crosstalk / growth factor signaling",
        "BATF3": "cDC1 cross-presentation — alternative antigen-presentation axis",
    }
    for gene in secondary:
        reason = known_roles.get(gene, "Supporting modifier from knowledge graph")
        result.append((gene, reason))
    return result


def _extract_predictions(justification: str, hypothesis: str, expanded: str) -> list[str]:
    """Extract 3 clinically testable predictions from the justification / hypothesis."""
    predictions = []

    # Look for numbered predictions in justification
    numbered = re.findall(r"\(\d+\)\s*(.*?)(?=\(\d+\)|$)", justification, re.DOTALL)
    if not numbered:
        numbered = re.findall(r"\d+\)\s*(.*?)(?=\d+\)|$)", justification, re.DOTALL)

    for n in numbered:
        text = n.strip()
        # Limit to first 2 sentences (max ~400 chars)
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
        clean = sentences[0].strip()
        if len(sentences) > 1 and len(clean) < 250:
            clean = clean + " " + sentences[1].strip()
        # Hard cap at 400 chars on a sentence boundary
        if len(clean) > 400:
            cut = clean[:400].rfind(". ")
            if cut > 100:
                clean = clean[: cut + 1]
        if len(clean) > 30:
            predictions.append(clean)

    # If we didn't find structured predictions, derive from content
    if len(predictions) < 3:
        # Prediction from interaction test
        if "interaction" in justification.lower() or "interaction" in hypothesis.lower():
            predictions.append(
                "In retrospective cohort data, the prognostic benefit of the imaging feature "
                "will be modified by the anchor gene expression level (significant interaction term)."
            )
        # Prediction from spatial
        if "spatial" in justification.lower() or "multiplex" in justification.lower():
            predictions.append(
                "At the tissue level, tumors with the imaging feature but low anchor gene activity "
                "will show immune cells confined to structural peripheries rather than infiltrating tumor nests."
            )
        # Prediction from functional
        if "functional" in justification.lower() or "ex vivo" in justification.lower() or "perturbation" in justification.lower():
            predictions.append(
                "In functional experiments, restoring or enhancing the anchor gene's signaling "
                "will improve immune-mediated tumor control in imaging-feature-positive models."
            )

    return predictions[:3] if predictions else [
        "The interaction between the imaging feature and anchor gene predicts clinical outcome.",
        "Spatial analysis will confirm the mechanistic link at the tissue level.",
        "Functional perturbation will establish causality.",
    ]


def _build_validation_plan(justification: str, coding_tasks: list[dict]) -> list[tuple[str, str]]:
    """Return a 3-tier validation plan: A (retrospective), B (tissue), C (functional)."""
    tiers = []

    # Tier A — Retrospective / computational
    tier_a_lines = ["**Data source:** TCGA-BLCA (Firehose Legacy), ~400 patients"]

    # Extract core analysis from coding tasks
    for ct in coding_tasks[:2]:  # max 2 tasks
        task_name = ct.get("task", "")
        desc = ct.get("description", "")
        # Simplify: take first sentence
        first_sent = desc.split(".")[0] + "." if desc else task_name
        tier_a_lines.append(f"- {first_sent}")

    if not coding_tasks:
        tier_a_lines.append("- Cox PH model with interaction term for the imaging feature and anchor gene")
        tier_a_lines.append("- Kaplan-Meier 2x2 stratification")

    tiers.append(("A. Retrospective Cohort Analysis", "\n".join(tier_a_lines)))

    # Tier B — Tissue-level validation
    tier_b = (
        "**Methods:** Multiplex IF / IHC / spatial transcriptomics\n\n"
        "**Key readouts:**\n"
        "- Immune structure markers (e.g., CD20, CD3, PNAd)\n"
        "- Effector cell localization (CD8) relative to tumor nests\n"
        "- Anchor gene protein / phospho-protein expression\n"
        "- Stromal features (collagen density by SHG, if applicable)\n\n"
        "**Goal:** Confirm that the proposed mechanism operates at the spatial / tissue level."
    )
    tiers.append(("B. Tissue-Level Validation", tier_b))

    # Tier C — Functional validation
    tier_c = (
        "**Models:** Ex vivo organotypic cultures, 3D co-culture, syngeneic mouse models\n\n"
        "**Perturbations:** Genetic (KO/rescue) or pharmacologic modulation of anchor gene\n\n"
        "**Readouts:** T-cell infiltration, cytotoxicity (granzyme B / IFN-gamma), tumor growth / survival\n\n"
        "**Goal:** Establish causal relationship between anchor gene activity and immune efficacy."
    )
    tiers.append(("C. Functional Validation", tier_c))

    return tiers


def _extract_strengths(justification: str, novelty: dict, summary: dict) -> list[str]:
    strengths = []
    # From hypothesis content
    if "interaction" in justification.lower():
        strengths.append(
            "Interaction-first biomarker strategy — asks WHY the imaging feature sometimes fails, "
            "not just whether it predicts outcome."
        )
    if "spatial" in justification.lower() and "functional" in justification.lower():
        strengths.append(
            "Multi-modal validation pipeline: computational -> spatial -> functional, "
            "providing mechanistic depth beyond association."
        )
    if "cell-intrinsic" in justification.lower() or "T-cell" in justification.lower() or "T\u2011cell" in justification.lower():
        strengths.append(
            "Focus on a cell-intrinsic mechanism provides a concrete, actionable target "
            "rather than a descriptive correlation."
        )
    # From summary agent
    innovations = _get(summary, "comprehensive_summary", "key_innovations")
    if innovations:
        # Truncate at sentence boundary within 250 chars
        trunc = innovations[:250]
        cut = trunc.rfind(". ")
        if cut > 50:
            trunc = trunc[: cut + 1]
        strengths.append(f"Key innovation: {trunc}")

    if not strengths:
        strengths.append("Novel mechanistic hypothesis derived from knowledge graph analysis.")
    return strengths


def _extract_caveats(justification: str, critical_eval: dict, novelty: dict) -> list[str]:
    caveats = []

    # From critical evaluation
    pitfalls = critical_eval.get("pitfalls_and_challenges", [])
    for p in pitfalls[:3]:
        caveats.append(p)

    # Common caveats if not already covered
    caveat_keywords = {
        "bulk RNA": "Bulk RNA-seq measurements may be confounded by cell-type abundance; normalization or single-cell validation is needed.",
        "expression.*activity": "Gene expression does not equal protein activity; phospho-protein or functional assays are needed for confirmation.",
        "direct.*evidence": "Direct evidence in the specific cancer type may be limited; the hypothesis extends findings from related contexts.",
    }
    if not caveats:
        for pattern, text in caveat_keywords.items():
            if re.search(pattern, justification, re.IGNORECASE):
                caveats.append(text)

    if not caveats:
        caveats.append("Hypothesis is computationally derived and requires experimental validation.")

    # From novelty rationale
    rationale = novelty.get("rationale", "")
    if rationale and len(caveats) < 3:
        caveats.append(f"Novelty assessment note: {rationale[:200]}")

    return caveats[:4]


def _extract_alternatives(secondary: list, notes: str, critical_eval: dict) -> list[str]:
    alternatives = []
    known_alt = {
        "FAP": "FAP / ITGB1 axis: TLS benefit blocked by stromal CAF barrier / ECM exclusion",
        "BATF3": "BATF3 / cDC1 axis: TLS value depends on cross-presentation capacity",
    }
    for gene in secondary:
        if gene in known_alt:
            alternatives.append(known_alt[gene])

    # From critical eval future avenues
    avenues = critical_eval.get("promising_avenues_for_future_research", [])
    for a in avenues[:2]:
        if len(a) < 300:
            alternatives.append(a)

    if not alternatives:
        for gene in secondary[:2]:
            alternatives.append(
                f"{gene}: Could serve as an alternative mechanistic axis if the primary hypothesis is not supported."
            )
    return alternatives[:3]


def _build_score_line(novelty: dict, novelty_scores: dict) -> str:
    parts = []
    # From multi_critic_coordinator
    if novelty.get("score"):
        parts.append(f"Novelty {novelty['score']}")
    if novelty.get("verdict"):
        parts.append(f"Verdict: {novelty['verdict']}")

    # From summary agent scores
    if novelty_scores.get("novelty_score"):
        parts.append(f"Novelty {novelty_scores['novelty_score']}")
    if novelty_scores.get("feasibility_score"):
        parts.append(f"Feasibility {novelty_scores['feasibility_score']}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# PDF conversion
# ---------------------------------------------------------------------------

def convert_md_to_pdf(md_file: str, pdf_file: str) -> None:
    with open(md_file, "r", encoding="utf-8") as f:
        md_content = f.read()

    html_content = markdown.markdown(md_content, extensions=["extra", "codehilite", "tables"])

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Research Hypothesis Report</title>
        <style>
            body {{
                font-family: "Segoe UI", Arial, sans-serif;
                margin: 30px 40px;
                line-height: 1.6;
                color: #1a1a1a;
            }}
            h1 {{
                color: #1a5276;
                border-bottom: 2px solid #1a5276;
                padding-bottom: 6px;
                font-size: 20px;
                margin-top: 32px;
            }}
            h2 {{
                color: #2c3e50;
                font-size: 16px;
                margin-top: 20px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
                margin: 12px 0;
                font-size: 13px;
            }}
            th, td {{
                border: 1px solid #bdc3c7;
                padding: 6px 10px;
                text-align: left;
            }}
            th {{
                background-color: #ecf0f1;
                font-weight: 600;
            }}
            blockquote {{
                border-left: 4px solid #3498db;
                margin: 16px 0;
                padding: 8px 16px;
                background-color: #eaf2f8;
                font-size: 13px;
            }}
            ul {{
                margin: 6px 0;
            }}
            li {{
                margin-bottom: 4px;
            }}
            strong {{
                color: #2c3e50;
            }}
            code {{
                background-color: #f4f4f4;
                padding: 1px 4px;
                font-size: 12px;
            }}
        </style>
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """

    with open(pdf_file, "wb") as f:
        pisa.CreatePDF(full_html, dest=f)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(input_txt: str) -> None:
    content = read_text_file(input_txt)
    print("File content loaded.")

    agent_dict = parse_agent_outputs(content)
    print("Agents found:", list(agent_dict.keys()))

    # Build clinician-friendly report
    md_content = build_clinician_report(agent_dict)

    # Write MD
    base_path = os.path.splitext(input_txt)[0]
    md_file = base_path + ".md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"Markdown file created: {md_file}")

    # Convert to PDF
    pdf_file = base_path + ".pdf"
    convert_md_to_pdf(md_file, pdf_file)
    print(f"PDF generated: {pdf_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process agent outputs from txt to MD and PDF.")
    parser.add_argument("input_txt", help="Path to the input .txt file")
    args = parser.parse_args()
    main(args.input_txt)
