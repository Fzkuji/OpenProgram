"""Official updates for an installation that runs from the version store.

Everything published is content-addressed: ``objects`` holds the object, pack
and layer-manifest format, ``channel`` the signed pointers clients read and the
transport that fetches byte ranges of packs, ``assemble`` turns layers into a
base and a version here, ``replay`` carries the owner's own commits over to
the new official code, ``fetch`` is what ``openprogram upgrade`` calls, and
``publish`` is the publisher's side. See
docs/reference/design/distribution/automatic-updates.html.
"""
