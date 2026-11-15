"""The built-in ONLYOFFICE editor, as an application.

Opens and edits Word, Excel and PowerPoint documents (and their OpenDocument
counterparts) in the App's document window with a pinned ONLYOFFICE build.
The editor itself is an optional component of about 730 MB that is
downloaded only when the user chooses Install.

- ``assets``: the pinned, verified Office pack and its atomic installation
  (standard library only; release tools load it by file path).
- ``install``: the explicit download of the pinned pack.
- ``host``: the isolated ``host-<session>.office.localhost`` origin, its
  manifest-bound files and the availability answer.
- ``service``: the ``office_editor`` service the server reaches all of this
  through.

There is no Agent entry. ``setup`` provides the ``office_editor`` service;
disposing the application's context withdraws it, and the server then
answers that the Office editor is turned off.
"""

#: What the Launchpad, Applications page and Abilities call this application
#: (``openprogram.programs._host.identity`` reads them from the source).
TITLE = "ONLYOFFICE"
DESCRIPTION = "Creates, opens and edits Word, Excel and PowerPoint documents in the App."
ICON = "office"


def setup(ctx) -> None:
    from .service import OfficeEditorService

    ctx.provide("office_editor", OfficeEditorService())
