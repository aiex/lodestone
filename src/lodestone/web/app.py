"""FastAPI app for the Lodestone dashboard.

create_app(config) builds the app; serve(config_path) runs it under uvicorn.
All JSON endpoints live under /api and require the token; the single HTML page
is served at / and bootstraps itself by calling those endpoints.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from ..config import load_config
from ..registry import db
from . import auth

STATIC_DIR = Path(__file__).parent / "static"


def create_app(config) -> FastAPI:
    app = FastAPI(title="Lodestone Dashboard", docs_url=None, redoc_url=None)
    db_path = config.db_path
    # Ensure the (additive) schema exists even against an old database.
    db.init_db(db_path)

    web = config.web
    app.state.token = (web.get("token") or "").strip() or auth.generate_token()

    def require_token(request: Request):
        if not auth.is_authorized(request, app.state.token):
            raise HTTPException(status_code=401, detail="missing or invalid token")
        return True

    guard = Depends(require_token)

    # --- the page ---------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index(request: Request):
        # First visit carries ?token=…; on a match we drop a cookie and bounce
        # to the clean URL so the secret leaves the address bar.
        q = request.query_params.get("token")
        if q and auth.is_authorized(request, app.state.token):
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(
                auth.COOKIE_NAME, app.state.token,
                httponly=True, samesite="strict", max_age=30 * 24 * 3600,
            )
            return resp
        if not auth.is_authorized(request, app.state.token):
            return JSONResponse(
                {"error": "unauthorized",
                 "hint": "open this URL with ?token=YOUR_TOKEN (see the lodestone "
                         "dashboard startup line for the link)"},
                status_code=401,
            )
        return FileResponse(STATIC_DIR / "index.html")

    # --- JSON API ---------------------------------------------------------
    @app.get("/api/stats")
    def stats(_=guard):
        agents = db.list_agents(db_path)
        projects = db.list_projects(db_path)
        totals = db.usage_totals(db_path)
        by_kind = {r["kind"]: r["count"] for r in db.activity_by_kind(db_path)}
        return {
            "agents": len(agents),
            "projects": len(projects),
            "dispatches": by_kind.get("dispatch", 0),
            "errors": by_kind.get("error", 0) + by_kind.get("timeout", 0),
            "llm_calls": totals["calls"],
            "total_tokens": totals["total_tokens"],
            "cost_usd": round(totals["cost_usd"], 4),
        }

    @app.get("/api/agents")
    def agents(_=guard):
        return db.list_agents(db_path)

    @app.get("/api/projects")
    def projects(_=guard):
        return db.list_projects(db_path)

    @app.get("/api/activity")
    def activity(limit: int = 50, _=guard):
        return {
            "recent": db.recent_logs(db_path, min(max(limit, 1), 500)),
            "by_kind": db.activity_by_kind(db_path),
            "by_agent": db.activity_by_agent(db_path, 10),
            "daily": db.activity_daily(db_path, 30),
        }

    @app.get("/api/usage")
    def usage(days: int = 30, _=guard):
        return {
            "totals": db.usage_totals(db_path),
            "by_model": db.usage_by_model(db_path),
            "by_agent": db.usage_by_agent(db_path),
            "daily": db.usage_daily(db_path, min(max(days, 1), 365)),
            "recent": db.recent_usage(db_path, 20),
        }

    return app


def serve(config_path: str = None) -> None:
    import uvicorn

    config = load_config(config_path)
    web = config.web
    host = web.get("host", "127.0.0.1")
    port = int(web.get("port", 8765))

    app = create_app(config)
    token = app.state.token
    shown_host = "127.0.0.1" if host in ("127.0.0.1", "0.0.0.0", "localhost") else host
    print("Lodestone dashboard")
    print(f"  open:  http://{shown_host}:{port}/?token={token}")
    if host == "0.0.0.0":
        print("  NOTE: host is 0.0.0.0 — the dashboard is reachable off-box. "
              "Keep it on 127.0.0.1 unless you've put auth/TLS in front.")
    uvicorn.run(app, host=host, port=port, log_level="info")
