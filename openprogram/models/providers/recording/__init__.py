"""Record, replay and capture provider requests.

``recording.py`` and ``replay.py`` record provider calls to a JSONL file and
replay them offline; ``capture.py`` captures the request body each adapter
sends, the one record of request bytes the prefix check and tests read.
"""
