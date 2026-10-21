import asyncio
import multiprocessing as mp
import os
import re
import sys
from pathlib import Path

# Load API keys from .env file
from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / '.env')
sys.path.append(str(PROJECT_ROOT))

import networkx as nx  # if needed elsewhere
from autogen_agentchat.messages import TextMessage
from huggingface_hub import hf_hub_download
from HypothesisGeneration.agents import create_multi_critic_coordinator
from transformers import AutoModel, AutoTokenizer

# Validate required keys
for key in ["OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "NCBI_API_KEY"]:
    if not os.environ.get(key):
        print(f"WARNING: {key} not set. Please configure it in .env file.")


def parse_hypotheses_from_md(md_text: str):
    """
    Parse hypotheses from a markdown string.

    Expects sections that start with lines like:

        Hypothesis 1:
        Hypothesis 2:
        ...

    Returns a list of (hypothesis_number:int, section_text:str).
    """
    pattern = re.compile(r"^Hypothesis\s+(\d+):\s*$", re.MULTILINE)
    matches = list(pattern.finditer(md_text))

    hypotheses = []
    for i, match in enumerate(matches):
        number = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        section_text = md_text[start:end].strip()
        hypotheses.append((number, section_text))

    return hypotheses


async def run_single_agent(task: str, hypothesis_number: int, output_dir: Path) -> None:
    """
    Create a Novelty Critic Agent, run it on a single task (hypothesis block),
    and write the last message content to a file.
    """

    # Create a fresh agent for this process
    novelty_critic_agent = create_multi_critic_coordinator(
        base_model="gpt-4o",
        literature_sources=["semantic_scholar", "pubmed"],
    )

    print(f"[Hypothesis {hypothesis_number}] 🤖 Novelty Critic Agent created")

    # Optional, if your framework needs it; the run(...) call only uses task.
    input_message = TextMessage(content=task, source="user")

    # === Your desired pattern ===
    # result_1 = await novelty_critic_agent.run(task=test_message)
    result_1 = await novelty_critic_agent.run(task=task)

    # Extract the last message content
    messages = result_1.__dict__.get("messages", [])
    if not messages:
        string_result = "No messages returned by novelty_critic_agent."
    else:
        last_msg = messages[-1]
        string_result = getattr(last_msg, "content", str(last_msg))

    # === Save to file ===
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"hypothesis_{hypothesis_number}.txt"

    file_contents = f"Hypothesis {hypothesis_number}:\n\n{string_result}\n"
    file_path.write_text(file_contents, encoding="utf-8")

    print(f"[Hypothesis {hypothesis_number}] ✅ Result saved to {file_path}")


def process_worker(task: str, hypothesis_number: int, output_dir_str: str) -> None:
    """
    Synchronous entrypoint for each process.
    Wraps the async logic in asyncio.run.
    """
    output_dir = Path(output_dir_str)
    try:
        asyncio.run(run_single_agent(task, hypothesis_number, output_dir))
    except Exception as e:
        print(f"[Hypothesis {hypothesis_number}] ❌ Error: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parallel_novelty_from_md.py path/to/hypotheses.md [output_dir]")
        sys.exit(1)

    md_path = Path(sys.argv[1]).expanduser().resolve()
    if not md_path.is_file():
        print(f"Markdown file not found: {md_path}")
        sys.exit(1)

    # Optional second argument: output directory
    if len(sys.argv) >= 3:
        output_dir = Path(sys.argv[2]).expanduser().resolve()
    else:
        output_dir = Path("novelty_results").absolute()

    # ---- Read and parse the markdown file ----
    md_text = md_path.read_text(encoding="utf-8")
    parsed = parse_hypotheses_from_md(md_text)

    if not parsed:
        print("No hypotheses found in the markdown file. Make sure they start with lines like 'Hypothesis 1:'.")
        sys.exit(1)

    # parsed is a list of (hypothesis_number, section_text)
    # We’ll sort by hypothesis_number just in case.
    parsed.sort(key=lambda x: x[0])

    # Prepare arguments for multiprocessing
    process_args = [(section_text, hypothesis_number, str(output_dir)) for hypothesis_number, section_text in parsed]

    num_processes = len(process_args)
    print(f"Found {num_processes} hypotheses in {md_path}")
    print(f"Results will be written into: {output_dir}")

    # Use 'spawn' for compatibility with asyncio + multiprocessing
    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=num_processes) as pool:
        pool.starmap(process_worker, process_args)
