from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse


router = APIRouter(tags=["admin-pages"])

_PAGE_FILES = {
    "/admin": "index.html",
    "/admin/raw": "admin-raw.html",
    "/admin/packages": "admin-packages.html",
    "/admin/jobs": "admin-jobs.html",
    "/admin/contacts": "admin-contacts-overview.html",
    "/admin/contacts/directory": "admin-contacts-directory.html",
    "/admin/contacts/imports": "admin-contacts-imports.html",
    "/admin/search": "admin-search.html",
    "/admin/system": "admin-system.html",
}
_DOMAIN_PAGE = "admin-domain.html"
_ALLOWED_DOMAINS = {"cn", "us-application", "us-assignment", "us-ttab"}
_ASSETS = {"admin.css", "admin.js"}


def _web_file(name: str) -> Path:
    for root in (Path("/app/web"), Path("web")):
        candidate = root / name
        if candidate.exists():
            return candidate
    raise HTTPException(status_code=404, detail=f"Admin asset not found: {name}")


def _page_handler(file_name: str) -> Callable[[], FileResponse]:
    def page() -> FileResponse:
        return FileResponse(_web_file(file_name))

    return page


for route_path, file_name in _PAGE_FILES.items():
    router.add_api_route(
        route_path,
        _page_handler(file_name),
        methods=["GET"],
        include_in_schema=False,
    )


@router.get("/admin/domains/{domain}", include_in_schema=False)
def admin_domain_page(domain: str):
    if domain not in _ALLOWED_DOMAINS:
        raise HTTPException(status_code=404, detail="Unknown Data Engine domain")
    return FileResponse(_web_file(_DOMAIN_PAGE))


@router.get("/admin/assets/{asset_name}", include_in_schema=False)
def admin_asset(asset_name: str):
    if asset_name not in _ASSETS:
        raise HTTPException(status_code=404, detail="Unknown admin asset")
    return FileResponse(_web_file(asset_name))
