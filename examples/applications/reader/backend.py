"""The same summary operation is available to the UI and Programs."""
from openprogram.agentic_programming import llm


def summarize(value, context):
    context.progress("Summarizing the supplied text")
    return llm("Summarize the following paper text. Identify its claims and limitations.\n\n" + value["text"])


operations = {"summarize": summarize}
