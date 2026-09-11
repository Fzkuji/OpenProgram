"""Save incremental results so a stopped run has inspectable progress."""


def analyze(value, context):
    results = []
    for relative in value["paths"]:
        text = context.read_file(relative)
        results.append({"path": relative, "lines": len(text.splitlines()), "characters": len(text)})
        current = context.load()
        context.save({"results": results}, expected_version=current["version"])
        context.progress({"completed": len(results), "total": len(value["paths"])})
    return results


operations = {"analyze": analyze}
