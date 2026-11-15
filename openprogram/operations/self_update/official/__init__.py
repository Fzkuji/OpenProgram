"""Official releases for an installation that runs from the version store.

``assets`` is the format a release publishes: the sealed version, the Git
bundle, the checksums and the signed manifest, both how they are made and how
they are checked and unpacked. ``replay`` takes one verified release and carries
the owner's own commits over to it.
"""
