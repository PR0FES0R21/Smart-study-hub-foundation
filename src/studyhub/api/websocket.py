import asyncio
import json
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from redis.exceptions import RedisError

from studyhub.api.dependencies import bind_school, load_principal
from studyhub.core.security import token_hash

router = APIRouter()


@router.websocket("/api/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    state = websocket.app.state
    origin = websocket.headers.get("origin")
    if origin and origin not in state.settings.allowed_origins:
        await websocket.close(code=1008)
        return
    ticket = websocket.query_params.get("ticket", "")
    if not 32 <= len(ticket) <= 200:
        await websocket.close(code=1008)
        return
    try:
        raw = await state.control.getdel(
            f"{state.settings.namespace}:ws-ticket:{token_hash(ticket)}"
        )
        if not raw:
            await websocket.close(code=1008)
            return
        claims = json.loads(raw)
        user_id, session_id = UUID(claims["user_id"]), UUID(claims["session_id"])
        school_id = UUID(claims["school_id"])
        async with state.sessions() as db:
            principal = await load_principal(db, user_id, session_id)
            if not principal:
                await websocket.close(code=1008)
                return
            await bind_school(db, principal, school_id)
    except (RedisError, ValueError, KeyError, TypeError, HTTPException):
        await websocket.close(code=1013)
        return
    try:
        connection = state.hub.connect(str(user_id), str(school_id))
    except ValueError:
        await websocket.close(code=1008)
        return
    try:
        await websocket.accept()
    except (WebSocketDisconnect, OSError, RuntimeError):
        state.hub.disconnect(connection)
        return

    async def writer():
        while True:
            event = await connection.queue.get()
            if event is None:
                await websocket.close(
                    code=1013, reason="Slow consumer; reconnect and fetch job status"
                )
                return
            await asyncio.wait_for(websocket.send_json(event), timeout=5)

    async def reader():
        last_auth = time.monotonic()
        messages = 0
        window = time.monotonic()
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=20)
                if len(message) > 128:
                    await websocket.close(code=1009)
                    return
                now = time.monotonic()
                if now - window >= 60:
                    window, messages = now, 0
                messages += 1
                if messages > 60 or json.loads(message) != {"type": "ping"}:
                    await websocket.close(code=1008)
                    return
                if not connection.queue.full():
                    connection.queue.put_nowait({"type": "pong"})
            except TimeoutError:
                if not connection.queue.full():
                    connection.queue.put_nowait({"type": "ping"})
            if time.monotonic() - last_auth >= 20:
                async with state.sessions() as db:
                    valid = await load_principal(db, user_id, session_id)
                    if valid:
                        try:
                            await bind_school(db, valid, school_id)
                        except HTTPException:
                            valid = None
                if not valid:
                    await websocket.close(code=1008)
                    return
                last_auth = time.monotonic()

    tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, TimeoutError, ValueError, RuntimeError):
        pass
    finally:
        state.hub.disconnect(connection)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
