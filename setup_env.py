"""
One-click environment setup script for Hypothesis Generation Agent.

Usage:
    python setup_env.py          # Full setup
    python setup_env.py --check  # Only check environment
"""

import subprocess
import sys
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def run_cmd(cmd, description, check=True):
    """Run a shell command with description."""
    print(f"\n>> {description}")
    print(f"   $ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0 and check:
        print(f"   FAILED: {result.stderr.strip()}")
        return False
    if result.stdout.strip():
        print(f"   {result.stdout.strip()[:200]}")
    return True


def check_python_version():
    """Check Python version >= 3.10."""
    v = sys.version_info
    print(f"\n[1/6] Python version: {v.major}.{v.minor}.{v.micro}")
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        print("  ERROR: Python >= 3.10 is required.")
        return False
    print("  OK")
    return True


def check_env_file():
    """Check .env file exists with required keys."""
    print("\n[2/6] Checking .env configuration...")
    env_path = PROJECT_ROOT / '.env'
    example_path = PROJECT_ROOT / '.env.example'

    if not env_path.exists():
        if example_path.exists():
            shutil.copy2(example_path, env_path)
            print("  Created .env from .env.example")
            print("  ACTION REQUIRED: Edit .env and fill in your API keys!")
            return False
        else:
            print("  ERROR: Neither .env nor .env.example found.")
            return False

    # Check for placeholder values
    content = env_path.read_text()
    missing = []
    for key in ["OPENAI_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "NCBI_API_KEY"]:
        if key not in content or f"{key}=your-" in content or f"{key}=" not in content:
            missing.append(key)

    if missing:
        print(f"  WARNING: These keys appear unconfigured in .env: {', '.join(missing)}")
        print("  ACTION REQUIRED: Edit .env and fill in your API keys!")
        return False

    print("  OK - .env file found with all keys configured")
    return True


def check_system_deps():
    """Check system-level dependencies."""
    print("\n[3/6] Checking system dependencies...")
    ok = True

    # Check wkhtmltopdf
    if shutil.which('wkhtmltopdf'):
        print("  wkhtmltopdf: OK")
    else:
        print("  wkhtmltopdf: NOT FOUND (optional, needed for PDF export)")
        print("    Install: https://wkhtmltopdf.org/downloads.html")
        # Not a hard requirement

    # Check git
    if shutil.which('git'):
        print("  git: OK")
    else:
        print("  git: NOT FOUND")

    return ok


def install_dependencies():
    """Install Python dependencies."""
    print("\n[4/6] Installing Python dependencies...")

    # Install requirements.txt
    req_path = PROJECT_ROOT / 'requirements.txt'
    if req_path.exists():
        if not run_cmd(
            f'"{sys.executable}" -m pip install -r "{req_path}"',
            "Installing from requirements.txt"
        ):
            return False
    else:
        print("  WARNING: requirements.txt not found, skipping.")

    return True


def install_local_packages():
    """Install local packages in editable mode."""
    print("\n[5/6] Installing local packages (editable mode)...")

    for pkg in ['GraphReasoning', 'HypothesisGeneration']:
        pkg_path = PROJECT_ROOT / pkg
        if (pkg_path / 'setup.py').exists():
            if not run_cmd(
                f'"{sys.executable}" -m pip install -e "{pkg_path}"',
                f"Installing {pkg}"
            ):
                return False
        else:
            print(f"  WARNING: {pkg}/setup.py not found, skipping.")

    return True


def check_data_files():
    """Check required data files exist."""
    print("\n[6/6] Checking data files...")

    required_files = [
        'inputs/initial_graph_biomed/global_graph_textbook_plus_papers.graphml',
    ]

    optional_files = [
        'inputs/initial_graph_biomed/global_graph_textbook_plus_papers.pkl',
        'novelty_validation_dataset.xlsx',
    ]

    ok = True
    for f in required_files:
        path = PROJECT_ROOT / f
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {f}: OK ({size_mb:.1f} MB)")
        else:
            print(f"  {f}: MISSING (required)")
            ok = False

    for f in optional_files:
        path = PROJECT_ROOT / f
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            print(f"  {f}: OK ({size_mb:.1f} MB)")
        else:
            print(f"  {f}: MISSING (optional, will be generated on first run)")

    return ok


def main():
    check_only = '--check' in sys.argv

    print("=" * 60)
    print("Hypothesis Generation Agent - Environment Setup")
    print("=" * 60)
    print(f"Project root: {PROJECT_ROOT}")

    results = {}
    results['python'] = check_python_version()
    results['env'] = check_env_file()
    results['system'] = check_system_deps()

    if not check_only:
        results['deps'] = install_dependencies()
        results['packages'] = install_local_packages()

    results['data'] = check_data_files()

    # Summary
    print("\n" + "=" * 60)
    print("SETUP SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, ok in results.items():
        status = "OK" if ok else "NEEDS ATTENTION"
        print(f"  {name:12s}: {status}")
        if not ok:
            all_ok = False

    if all_ok:
        print("\nAll checks passed! You can now run the notebooks.")
        print("  cd notebooks && jupyter notebook initial_global_graph.ipynb")
    else:
        print("\nSome checks need attention. See details above.")

    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
