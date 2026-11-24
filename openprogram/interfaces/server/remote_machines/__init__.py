"""Remote OpenProgram machines: another computer's conversations, listed and driven from here.

A machine with an ``openprogram`` connection (``openprogram.host.machines.peer``)
is another computer running OpenProgram, paired with this one. ``links``
keeps one connection to each and merges their conversations and projects
into this computer's lists; ``forward`` sends every action on one of
them to the computer it lives on and passes the answers back to the page
that asked. Nothing is copied: transcripts, files and accounts stay there.

Design: docs/reference/design/integrations/remote-openprogram.html
"""
