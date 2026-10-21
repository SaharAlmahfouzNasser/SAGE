from dataclasses import dataclass, field

@dataclass
class DomainConfig:
    disease: str                          # e.g. "breast cancer"
    tcga_cohort: str                      # e.g. "TCGA-BRCA"
    tcga_full_name: str                   # e.g. "Breast Invasive Carcinoma (Firehose Legacy)"
    n_patients: str                       # e.g. "~1,000"
    available_endpoints: list[str]        # e.g. ["OS", "PFI", "DSS"]
    available_imaging_features: list[str]
    available_gene_sets: dict[str, list[str]]  # name -> gene list
    therapy_proxies: list[str]
    specialist_description: str           # e.g. "specializing in breast cancer"

BREAST_CANCER = DomainConfig(
    disease="breast cancer",
    tcga_cohort="TCGA-BRCA",
    tcga_full_name="Breast Invasive Carcinoma (Firehose Legacy)",
    n_patients="~1,000",
    available_endpoints=["OS", "PFI", "DSS"],
    available_imaging_features=[
        "Tumor-Infiltrating Lymphocyte (TIL) density",
        "TLS-like lymphoid aggregates",
        "Stromal-to-tumor ratio (TSR)",
        # ... etc
    ],
    available_gene_sets={
        "NABA_CORE_MATRISOME": ["COL1A1", "COL3A1", "FN1", "LAMA2"],
        "T-cell Exhaustion": ["PDCD1", "CTLA4", "LAG3", "TIGIT"],
    },
    therapy_proxies=[
        "Hormone receptor signaling (ESR1, PGR)",
        "HER2 signaling (ERBB2)",
    ],
    specialist_description="specializing in breast cancer",
)

BLADDER_CANCER = DomainConfig(
    disease="bladder cancer",
    tcga_cohort="TCGA-BLCA",
    tcga_full_name="Bladder Urothelial Carcinoma (Firehose Legacy)",
    n_patients="~400",
    available_endpoints=["OS", "PFI"],
    available_imaging_features=[
        "TIL density",
        "Necrosis percentage",
        "Nuclear pleomorphism",
    ],
    available_gene_sets={
        "FGFR pathway": ["FGFR3", "FGFR1", "FGF19"],
        "T-cell Exhaustion": ["PDCD1", "CTLA4", "LAG3"],
    },
    therapy_proxies=[
        "Immune checkpoint response (PD-L1, TMB)",
        "FGFR inhibitor response (FGFR3 mutation)",
    ],
    specialist_description="specializing in bladder cancer",
)