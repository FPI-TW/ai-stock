"""LOCAL_MODE-only test page router.

Serves the single-page API tester at ``GET /test``. The static assets it
references live under ``/test-assets/*`` and are mounted directly in
``main.create_app()`` via ``StaticFiles``.

Deployment assumption: the FastAPI app is bound to 127.0.0.1 (developer
machine or Docker compose loopback). This router is only included from
``main.py`` when ``LOCAL_MODE=true`` — the same gate guards ``/dev/*``.
If LOCAL_MODE is ever exposed beyond loopback, this page exposes the
multi-user / push-quote / set-clock surface area and must be re-gated
behind auth before deployment.
"""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_STATIC_TEST_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "test"
_INDEX_HTML = _STATIC_TEST_DIR / "index.html"


@router.get("/test", include_in_schema=False)
def test_page() -> FileResponse:
    """Serve the API tester single-page HTML."""
    return FileResponse(_INDEX_HTML, media_type="text/html")
