"""The kiosk's local web app (http://127.0.0.1 only).

Serves the kiosk screen (camera stream, live state, Yes/No answers) and forwards the
admin portal's API calls to the school server, so Ctrl+Shift+A works on the kiosk.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from backend.central.app import FRONTEND_DIST, LOCAL_HOSTS, client_ip, mount_frontend


class AnswerBody(BaseModel):
    answer: Literal["yes", "no"]


def create_kiosk_app(runtime, proxy: httpx.Client,
                     frontend_dir: Path = FRONTEND_DIST) -> FastAPI:
    controller = runtime.controller
    app = FastAPI(title="PAS kiosk", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if client_ip(request) not in LOCAL_HOSTS:
            return Response("This only works on the kiosk itself.", status_code=403)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def local_only(request: Request) -> None:
        if client_ip(request) not in LOCAL_HOSTS:
            raise HTTPException(403, "This only works on the kiosk itself.")

    @app.get("/api/stream", dependencies=[Depends(local_only)])
    async def stream():
        async def frames():
            last = -1
            while True:
                frame_id, jpeg = await asyncio.to_thread(runtime.camera.wait_for_frame, last, 1.0)
                if frame_id == last or not jpeg:
                    last = frame_id
                    continue
                last = frame_id
                yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/api/ws")
    async def screen_socket(websocket: WebSocket):
        if not websocket.client or websocket.client.host not in LOCAL_HOSTS:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        last = None
        try:
            while True:
                state = await asyncio.to_thread(runtime.screen_state)
                if state != last:
                    await websocket.send_json(state)
                    last = state
                await asyncio.sleep(0.1)
        except Exception:
            pass  # the browser went away

    @app.post("/api/kiosk/answer", dependencies=[Depends(local_only)])
    def kiosk_answer(body: AnswerBody):
        return {"accepted": controller.answer(body.answer)}

    @app.post("/api/kiosk/recheck", dependencies=[Depends(local_only)])
    def kiosk_recheck():
        runtime.request_recheck()
        runtime.sync.wake()
        return {"ok": True}

    @app.get("/api/kiosk/info", dependencies=[Depends(local_only)])
    def kiosk_info():
        return {"device_name": runtime.store.get("device_name") or None}

    @app.api_route("/api/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE"],
                   dependencies=[Depends(local_only)])
    async def admin_proxy(path: str, request: Request):
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() in ("authorization", "content-type")}
        try:
            upstream = await asyncio.to_thread(
                proxy.request, request.method, f"/api/admin/{path}",
                params=request.query_params, content=await request.body(), headers=headers)
        except httpx.HTTPError:
            return Response('{"detail": "Cannot reach the attendance server."}',
                            status_code=502, media_type="application/json")
        return Response(upstream.content, status_code=upstream.status_code,
                        media_type=upstream.headers.get("content-type"))

    mount_frontend(app, frontend_dir)
    return app
