"""Remote OpenProgram machines: another computer's conversations, listed and driven from here.

A machine reached by the method ``openprogram`` (``openprogram.host.machines.peer``)
is another computer running OpenProgram, paired with this one. ``links``
keeps one connection to each and merges their conversations and projects
into this computer's lists; ``forward`` sends every action on one of
them to the computer it lives on and passes the answers back to the page
that asked. Nothing is copied: transcripts, files and accounts stay there.
``computer_use`` is the receiving end of the other direction: it runs the
``computer_use`` calls a paired device forwards here, while the owner lets
paired devices operate this computer.

Design: docs/reference/design/integrations/remote-openprogram.html
"""
