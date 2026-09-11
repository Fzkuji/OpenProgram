from openprogram.context.tokens import count_tokens, estimate_history_tokens


def test_count_tokens_compatibility_entry_point():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    assert count_tokens(messages, model=object()) == estimate_history_tokens(messages)
