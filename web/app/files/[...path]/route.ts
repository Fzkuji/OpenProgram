/**
 * Proxy for ``/files/*`` — the backend's raw project-file endpoint
 * (``GET /files/raw?project_id=..&path=..``) used by the files panel
 * for images and downloads.
 *
 * Reuses the ``/api/*`` catch-all proxy, which re-reads the worker's
 * port file per request (a ``next.config.mjs`` rewrite would bake the
 * port in at boot — see the comments in the api route). The proxy
 * forwards ``req.nextUrl.pathname`` verbatim, so ``/files/raw`` here
 * reaches ``/files/raw`` on the worker.
 */
import { GET as proxyGET, HEAD as proxyHEAD } from "../../api/[...path]/route";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const GET = proxyGET;
export const HEAD = proxyHEAD;
