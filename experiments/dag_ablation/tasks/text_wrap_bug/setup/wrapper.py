def wrap(text, width):
    """Greedy-wrap text to lines of at most `width` chars."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) < width:   # BUG: off-by-one, ignores space
            cur = cur + " " + w
        else:
            lines.append(cur)
            cur = w
    return lines                        # BUG: drops the last line


def indent(text, prefix):
    return "\n".join(prefix + line for line in text.split("\n"))
