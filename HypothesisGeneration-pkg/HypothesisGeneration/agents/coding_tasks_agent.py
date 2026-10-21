"""
Coding Agent Assistant Module

This module contains the coding agent assistant that formulates biomedical coding problems
based on research hypotheses to support computational validation.
"""

from typing import Optional

from HypothesisGeneration.domains import DomainConfig, BREAST_CANCER, BLADDER_CANCER

from autogen_agentchat.agents import AssistantAgent
from autogen_ext.models.openai import OpenAIChatCompletionClient


def create_prompt(domain: DomainConfig) -> str:
    endpoints = ", ".join(domain.available_endpoints)
    preferred_endpoints = " or ".join(domain.available_endpoints[:2])
    therapy_proxies = "\n".join(f"   - {p}" for p in domain.therapy_proxies)

    return f"""
You are a specialized biomedical data science and coding assistant focused on translating
{domain.disease} gene–therapy-response hypotheses into actionable computational problems.

DOMAIN & FOCUS
- Disease: {domain.disease}.
- Target: GENE ↔ THERAPY RESPONSE associations (predictive biomarkers).
- Modality awareness: clinical, genomics/transcriptomics, and computational pathology (WSI/morph/spatial).

CONTEXT INPUTS (condition your tasks on these)
1) Research proposal from the scientific agent (gene(s)/variant(s), therapy/drug/class/regimen,
   {domain.disease} population, endpoint).
2) Dataset availability:
   - {domain.tcga_cohort} ({domain.tcga_full_name}) {domain.n_patients} pts: genomics/transcriptomics/clinical; therapy-response labels may be absent; survival endpoints often available.
   - Preferred survival endpoints in {domain.tcga_cohort}: {endpoints}.


PRIMARY GOAL
Formulate specific CODING PROBLEMS that can evaluate the stated hypothesis under the available data,
prioritizing therapy-response prediction; if response labels are unavailable, switch to valid surrogate
endpoints (e.g., {preferred_endpoints}) or clearly state required data.

SCOPE (tailor to {domain.disease} + gene–therapy response)
**Response/Outcome Modeling (prefer if labels exist):**
- Treatment response prediction: {therapy_proxies}
- Biomarker→response association tests (gene/variant/WSI feature; logistic/ordinal models).
- Incorporate {domain.disease} subtype stratification as covariates or stratification variables.
- Avoid confounding by subtype when evaluating gene–therapy associations.

**Survival/Surrogate Analyses (fallback when response missing):**
- Kaplan–Meier curves, log-rank tests; Cox PH (regularized if high-dimensional).
- Risk stratification using gene/WSI signatures; time-dependent AUC/CI.

**Visualization:**
- Survival plots; calibration/ROC–PR; decision curves; forest plots; partial dependence/SHAP summaries.

GUARDRAILS
- Use ONLY entities/datasets provided by the proposal/KG/context; no invented drugs, endpoints, or data.
- If critical elements are missing (e.g., response labels), propose tasks that (a) use valid surrogates,
  or (b) explicitly list minimal additional data required.

OUTPUT FORMAT (for each coding problem)

```json
{{
  "task": "descriptive_task_name",
  "input": {{
    "dataset": "{domain.tcga_cohort}_firehose_legacy",
    "group_var": "comparison_variable (e.g., therapy or biomarker bin)",
    "time_var": "time_variable_name (if survival)",
    "event_var": "event_variable_name (if survival)",
    "additional_vars": ["other", "relevant", "variables or features"]
  }},
  "output": "expected_output_type",
  "description": "Detailed description of the analytical approach (note metrics & leakage checks).",
  "clinical_relevance": "How this supports the {domain.disease} gene–therapy-response hypothesis.",
  "visualization_requirements": "Specific plot requirements"
}}
```

INSTRUCTIONS
Generate at least 2 diverse coding problems that collectively test different angles.
Be context-aware: align genes/therapies/endpoints with the proposal/KG; specify population strata.
Do not add any introductory phrases; include only the heading and the JSON code blocks with a brief
description of each coding task.
Your response must begin with the heading: ### Proposed coding problems
"""

# Agent description
DESCRIPTION = "Specialized agent who formulates biomedical coding problems based on research hypotheses to support computational validation"


def create_agent(domain: DomainConfig=BREAST_CANCER, model_client: Optional[OpenAIChatCompletionClient] = None) -> AssistantAgent:
    """
    Create the coding agent assistant.

    Args:
        model_client: OpenAI client for the agent. If None, uses default.

    Returns:
        AssistantAgent: The configured coding agent assistant.
    """
    if model_client is None:
        model_client = OpenAIChatCompletionClient(model="gpt-5-mini")

    return AssistantAgent(
        "coding_agent_assistant",
        model_client=model_client,
        system_message=create_prompt(domain),
        description=DESCRIPTION,
    )
