# Task: implement the `tally` CLI

Write `tally.py`, a command-line tool. Behaviour:

    python tally.py count FILE           -> "<n> lines"
    python tally.py count FILE --words   -> "<n> words"
    python tally.py top FILE -n K        -> K lines "<word> <count>",
                                            most frequent first,
                                            ties broken alphabetically
    python tally.py                      -> usage on stderr, exit 2

Words are whitespace-separated, lowercased, stripped of leading and
trailing punctuation. Missing file: print "no such file: PATH" to
stderr and exit 1.

`test_tally.py` drives the script as a subprocess. Do not edit it.
Run `python -m pytest -q` until green.
