"""
Code Review — Agentic function example.

Uses agentic functions to review code: read file → analyze → generate report.

This example demonstrates:
    - Chaining agentic functions for a multi-step workflow
    - Using runtime.exec() for LLM-powered analysis
    - Accessing Context trees for inspection

Usage:
    # With a real LLM provider:
    OPENAI_API_KEY=your_key python examples/code_review.py

    # The example below uses a mock for demonstration.
"""

from agentic import agentic_function, Runtime


# ── Mock LLM (replace with a real provider for actual use) ─────

def mock_llm(content, model="default", response_format=None):
    """Mock LLM that simulates code review responses."""
    text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")

    if "read" in text.lower() or "extract" in text.lower():
        return (
            "Found 3 functions:\n"
            "- process_data(items): loops through items, no type hints\n"
            "- save_to_db(data): raw SQL, no parameterized queries\n"
            "- handle_request(req): catches bare Exception"
        )
    elif "analyze" in text.lower() or "issue" in text.lower():
        return (
            "Issues found:\n"
            "1. [HIGH] SQL injection risk in save_to_db() — use parameterized queries\n"
            "2. [MEDIUM] Bare Exception catch in handle_request() — catch specific exceptions\n"
            "3. [LOW] Missing type hints in process_data() — add type annotations\n"
            "4. [LOW] No docstrings on any function"
        )
    else:
        return (
            "# Code Review Report\n\n"
            "## Summary\n"
            "Reviewed 3 functions. Found 1 high, 1 medium, and 2 low severity issues.\n\n"
            "## Critical: SQL Injection in save_to_db()\n"
            "Replace string formatting with parameterized queries.\n\n"
            "## Recommendation\n"
            "Fix the SQL injection issue immediately. Add type hints and docstrings "
            "in a follow-up PR.\n\n"
            "**Overall: Needs changes before merge.**"
        )


# ── Runtime ─────────────────────────────────────────────────────

runtime = Runtime(call=mock_llm, model="gpt-4o")


# ── Agentic Functions ──────────────────────────────────────────

@agentic_function
def read_code(file_path: str):
    """Read a source file and extract its structure — functions, classes, and key logic."""
    # Python runtime: read the file
    try:
        with open(file_path, "r") as f:
            code = f.read()
    except FileNotFoundError:
        code = (
            "def process_data(items):\n"
            "    for item in items:\n"
            "        result = item['value'] * 2\n"
            "    return result\n\n"
            "def save_to_db(data):\n"
            "    query = f\"INSERT INTO records VALUES ('{data}')\"\n"
            "    db.execute(query)\n\n"
            "def handle_request(req):\n"
            "    try:\n"
            "        return process(req)\n"
            "    except Exception:\n"
            "        return 'error'\n"
        )

    # LLM runtime: understand the code structure
    return runtime.exec(content=[
        {"type": "text", "text": f"Read and extract the structure of this code:\n\n```python\n{code}\n```"},
    ])


@agentic_function
def analyze_issues(code_summary: str):
    """Analyze code for bugs, security issues, and style problems."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Analyze this code for issues. "
            f"Categorize by severity (HIGH/MEDIUM/LOW).\n\n{code_summary}"
        )},
    ])


@agentic_function(compress=True)
def generate_report(issues: str):
    """Generate a final review report with actionable recommendations."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Generate a code review report from these issues.\n"
            f"Include: summary, critical items, and recommendations.\n\n{issues}"
        )},
    ])


@agentic_function
def review_code(file_path: str):
    """Full code review pipeline: read → analyze → report."""
    structure = read_code(file_path)
    issues = analyze_issues(structure)
    report = generate_report(issues)
    return report


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    report = review_code("app.py")

    print("── Review Report ──")
    print(report)

    print("\n── Context Tree ──")
    print(review_code.context.tree())
