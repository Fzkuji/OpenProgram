"""Self-check after a write: pose a question, retrieve it back, repair."""

VERIFY_PROBE = """Select one concrete factual detail from this conversation that should be recoverable from long-term memory.

Output only JSON with this shape:
{{"question":"a natural factual question","expected_answer":"the source-grounded answer","refs":["provider/thread_id/message_id"]}}

Observation date:
{observation_date}

Conversation:
{conversation}"""

VERIFY_RETRIEVE = """Answer this question using the supplied memory workspace.

Inspect whichever memory views, files, and sections you consider appropriate. Do not assume where the answer should be stored. Do not change any file.

Question: {question}

After inspection, output exactly one <answer>...</answer> block."""

VERIFY_JUDGE = """Decide whether the retrieved answer correctly answers the question according to the expected source-grounded answer.

Question: {question}
Expected answer: {expected_answer}
Retrieved answer: {retrieved_answer}"""

VERIFY_REPAIR = """Repair the memory workspace so that the question can be answered through the memory organization.

Inspect the cited conversation, the existing memory, and the retrieval trace. Make any changes you consider useful. You may add, revise, move, merge, reorder, or remove memory content while preserving valid historical information and source grounding.

Apply all changes only to editable Topic or Core memory.

Question: {question}
Expected source-grounded answer: {expected_answer}
Source references: {refs}
Previous retrieval answer: {retrieved_answer}
Previous retrieval trace:
{trace}"""
