"""Reference solution: writes summary.json using logtool."""
import json

import logtool

json.dump({
    "levels": logtool.count_by("server.log", "level"),
    "services": logtool.count_by("server.log", "service"),
    "corruption_hits": len(logtool.search("server.log", "ledger corruption")),
}, open("summary.json", "w"), indent=2)
