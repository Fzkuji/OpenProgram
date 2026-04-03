"""
Data Analysis — Agentic function example.

Uses agentic functions to analyze data: describe → find trends → summarize.

This example demonstrates:
    - Mixing Python data processing with LLM reasoning
    - Using render levels to control context visibility
    - Structured output via response_format

Usage:
    # With a real LLM provider:
    OPENAI_API_KEY=your_key python examples/data_analysis.py

    # The example below uses a mock for demonstration.
"""

from agentic import agentic_function, Runtime


# ── Mock LLM ───────────────────────────────────────────────────

def mock_llm(content, model="default", response_format=None):
    """Mock LLM for data analysis."""
    text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")

    if "describe" in text.lower() or "statistic" in text.lower():
        return (
            "Dataset has 12 monthly records (Jan–Dec 2024).\n"
            "Revenue: mean=$142K, min=$95K (Feb), max=$210K (Nov)\n"
            "Users: mean=45K, growing from 28K to 67K\n"
            "Churn: mean=4.2%, range 2.8%–6.1%"
        )
    elif "trend" in text.lower() or "pattern" in text.lower():
        return (
            "Key trends:\n"
            "1. Revenue grew 120% YoY with seasonal dip in Feb\n"
            "2. User growth is linear (~3.5K/month), accelerating in Q4\n"
            "3. Churn spiked in Mar (6.1%) — correlates with pricing change\n"
            "4. Revenue per user declining: $3.39→$3.13 (more free-tier users)"
        )
    else:
        return (
            "# Analysis Summary\n\n"
            "Strong growth year: revenue +120%, users +139%. "
            "However, revenue per user is declining 8%, suggesting the growth "
            "is coming from lower-value segments.\n\n"
            "**Action items:**\n"
            "1. Investigate Feb revenue dip — seasonal or fixable?\n"
            "2. Address Mar churn spike — likely pricing-related\n"
            "3. Consider tiered pricing to improve revenue per user"
        )


# ── Runtime ─────────────────────────────────────────────────────

runtime = Runtime(call=mock_llm, model="gpt-4o")


# ── Sample Data ────────────────────────────────────────────────

MONTHLY_DATA = [
    {"month": "Jan", "revenue": 95000, "users": 28000, "churn": 3.2},
    {"month": "Feb", "revenue": 98000, "users": 31000, "churn": 3.5},
    {"month": "Mar", "revenue": 110000, "users": 34000, "churn": 6.1},
    {"month": "Apr", "revenue": 125000, "users": 38000, "churn": 4.8},
    {"month": "May", "revenue": 132000, "users": 41000, "churn": 4.2},
    {"month": "Jun", "revenue": 138000, "users": 43000, "churn": 3.9},
    {"month": "Jul", "revenue": 145000, "users": 46000, "churn": 3.7},
    {"month": "Aug", "revenue": 155000, "users": 49000, "churn": 3.5},
    {"month": "Sep", "revenue": 168000, "users": 53000, "churn": 4.0},
    {"month": "Oct", "revenue": 185000, "users": 58000, "churn": 4.5},
    {"month": "Nov", "revenue": 210000, "users": 63000, "churn": 2.8},
    {"month": "Dec", "revenue": 198000, "users": 67000, "churn": 3.4},
]


# ── Agentic Functions ──────────────────────────────────────────

@agentic_function(render="detail")
def describe_data(data: list):
    """Compute basic statistics and describe the dataset structure."""
    # Python runtime: compute stats
    revenues = [d["revenue"] for d in data]
    users = [d["users"] for d in data]
    churns = [d["churn"] for d in data]

    stats = (
        f"Records: {len(data)}\n"
        f"Revenue — min: ${min(revenues):,}, max: ${max(revenues):,}, "
        f"mean: ${sum(revenues)//len(revenues):,}\n"
        f"Users — min: {min(users):,}, max: {max(users):,}, "
        f"mean: {sum(users)//len(users):,}\n"
        f"Churn — min: {min(churns):.1f}%, max: {max(churns):.1f}%, "
        f"mean: {sum(churns)/len(churns):.1f}%"
    )

    # LLM runtime: interpret the statistics
    return runtime.exec(content=[
        {"type": "text", "text": f"Describe this dataset:\n\n{stats}"},
    ])


@agentic_function(render="detail")
def find_trends(data: list, description: str):
    """Analyze the data for trends, patterns, and anomalies."""
    # Python runtime: compute month-over-month changes
    changes = []
    for i in range(1, len(data)):
        prev, curr = data[i - 1], data[i]
        rev_change = (curr["revenue"] - prev["revenue"]) / prev["revenue"] * 100
        changes.append(f"{curr['month']}: revenue {rev_change:+.1f}%, "
                       f"users {curr['users'] - prev['users']:+,}, "
                       f"churn {curr['churn']:.1f}%")

    changes_text = "\n".join(changes)

    # LLM runtime: find patterns
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Dataset description: {description}\n\n"
            f"Month-over-month changes:\n{changes_text}\n\n"
            f"Identify key trends and anomalies."
        )},
    ])


@agentic_function(compress=True, render="result")
def summarize_findings(description: str, trends: str):
    """Synthesize all findings into an executive summary with action items."""
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Data overview: {description}\n\n"
            f"Trends: {trends}\n\n"
            f"Write an executive summary with concrete action items."
        )},
    ])


@agentic_function
def analyze(data: list):
    """Full data analysis pipeline: describe → trends → summary."""
    desc = describe_data(data)
    trends = find_trends(data, desc)
    summary = summarize_findings(desc, trends)
    return summary


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    result = analyze(MONTHLY_DATA)

    print("── Analysis Result ──")
    print(result)

    print("\n── Context Tree ──")
    print(analyze.context.tree())
