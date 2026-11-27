"""One application package run as software of its own, outside the App.

``openprogram programs export`` (:mod:`openprogram.programs._host.ui.export`) writes an application
package, a launcher and, when it can, the Python runtime into a folder or
a macOS ``.app``. Launching it runs :mod:`.server`: the same application
runtime the App's worker runs (catalog, runners, state, recordings, host
calls) for that one package, with its own data folder
(``OPENPROGRAM_DATA_DIR``), behind a page of its own (``shell/``) that
gives the window the bridge the App's tab gives it. No worker, web app,
chat, channel or scheduler runs.

What the App answers from its own services, the standalone host answers
itself: conversations (capability ``conversation``) are kept in its data
folder and answered by the configured model (:mod:`.conversations`);
model and voice settings come from the OpenProgram on the same computer
when there is one (:mod:`.settings`).
"""
