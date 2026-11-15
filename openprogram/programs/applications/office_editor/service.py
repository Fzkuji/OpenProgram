"""The ``office_editor`` service: everything of the Office editor the server reaches.

The application's ``setup`` provides one :class:`OfficeEditorService` as the
service ``office_editor``. The server never imports the application; it
looks the service up (``openprogram.programs._host.services.lookup("office_editor")``)
and, when the lookup returns ``None``, answers that the Office editor is
turned off: the document window then offers only the original-file download.

Members:

- ``load()`` / ``pack``: the installed, verified Office pack, read once and
  kept; ``pack`` can be replaced (an installation does that).
- ``is_office_host(scope, port)`` / ``serve_asset(...)``: the isolated
  ``host-<session>.office.localhost`` origin and its manifest-bound files.
- ``availability(request)``: what ``GET /api/documents/office-host`` answers.
- ``install_rejection(request)``: why an installation request is refused,
  or ``None``.
- ``install()``: download, verify and select the pinned pack (blocking);
  ``INSTALL_ERRORS`` are the failures a caller reports as retryable;
  ``UNPUBLISHED_ERRORS`` means no release this version reads carries the
  archive, which a retry cannot fix.
- ``module_version`` / ``module_response(port)``: the parent module the
  document window imports from the main origin.
"""
from __future__ import annotations

import threading
import zipfile
from typing import Optional

import httpx

from . import host
from .assets import OFFICE_PATCH_SHA256, OfficeAssetPack
from .install import OfficeArchiveUnpublished


class OfficeEditorService:
    """The OnlyOffice editor, as the server reaches it."""

    #: Failures of ``install()`` that leave the previous state and can be retried.
    INSTALL_ERRORS = (OSError, ValueError, RuntimeError, httpx.HTTPError, zipfile.BadZipFile)
    #: The archive is not published for this version; checked before ``INSTALL_ERRORS``.
    UNPUBLISHED_ERRORS = (OfficeArchiveUnpublished,)
    module_version = OFFICE_PATCH_SHA256

    def __init__(self, pack: Optional[OfficeAssetPack] = None) -> None:
        self._pack = pack
        self._lock = threading.Lock()

    def load(self) -> OfficeAssetPack:
        """The installed pack, verified on first use (it hashes every file)."""
        with self._lock:
            if self._pack is None:
                self._pack = host.load_installed_office_pack()
            return self._pack

    @property
    def pack(self) -> OfficeAssetPack:
        return self.load()

    @pack.setter
    def pack(self, value: OfficeAssetPack) -> None:
        with self._lock:
            self._pack = value

    @staticmethod
    def is_office_host(scope, port: int) -> bool:
        return host.is_office_host(scope, port)

    async def serve_asset(self, scope, receive, send, port: int, frame_ancestors: str) -> None:
        await host.serve_asset(scope, receive, send, self.pack, port, frame_ancestors)

    def availability(self, request) -> dict:
        return host.office_host_availability(request, self.pack)

    @staticmethod
    def install_rejection(request) -> dict | None:
        return host.office_install_rejection(request)

    def install(self) -> OfficeAssetPack:
        """Download and select the pinned pack, unless one is already available."""
        if self.pack.available:
            return self.pack
        from . import install
        self.pack = install.download_office_pack()
        return self.pack

    def module_response(self, port: int):
        return host.ParentModuleResponse(self.pack, port)
