"""
Unified LLM Gateway FastAPI Server.
Provides an OpenAI-compatible interface secured by ONE Unified API Key.
"""
from contextlib import asynccontextmanager
from typing import Dict, Any, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import GatewayConfig, get_or_create_unified_key
from .router import DynamicRouter


router_engine: Optional[DynamicRouter] = None
unified_key: str = ""
config = GatewayConfig()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global router_engine, unified_key
    unified_key = get_or_create_unified_key()
    config.unified_api_key = unified_key
    router_engine = DynamicRouter(config)
    await router_engine.start_background_discovery()
    yield
    if router_engine:
        await router_engine.close()


app = FastAPI(
    title="Unified Multi-Provider LLM Gateway",
    description="Intelligent LLM Gateway unifying 32 providers under ONE API Key with dynamic per-model quota routing.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_unified_key(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
):
    """Verifies that request comes with the correct Unified API Key."""
    provided_key = None
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            provided_key = parts[1]
        elif len(parts) == 1:
            provided_key = parts[0]
    elif x_api_key:
        provided_key = x_api_key

    if not provided_key or provided_key.strip() != unified_key.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid or missing Unified API Key. Provide 'Authorization: Bearer <UNIFIED_KEY>' header.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key"
                }
            }
        )
    return provided_key


@app.get("/")
async def root():
    return {
        "status": "online",
        "service": "Unified Multi-Provider LLM Gateway",
        "auth_required": True,
        "docs": "/docs",
        "models_endpoint": "/v1/models",
        "chat_endpoint": "/v1/chat/completions",
        "stats_endpoint": "/stats"
    }


@app.get("/health")
@app.get("/v1/health")
async def health_check():
    if not router_engine:
        return JSONResponse(status_code=503, content={"status": "initializing"})
    
    stats = router_engine.quota_tracker.get_summary_stats()
    return {
        "status": "healthy",
        "active_providers_count": len(router_engine.active_providers),
        "total_monitored_models": stats["total_monitored_models"],
        "healthy_models": stats["healthy"],
        "cooldown_models": stats["cooldown"],
        "exhausted_models": stats["exhausted"]
    }


@app.get("/stats")
async def get_gateway_stats(token: str = Depends(verify_unified_key)):
    """Returns detailed real-time per-model quota and health statistics."""
    if not router_engine:
        raise HTTPException(status_code=503, detail="Gateway initializing")
    return router_engine.quota_tracker.get_summary_stats()


@app.get("/v1/models")
async def list_models(token: str = Depends(verify_unified_key)):
    """OpenAI-compatible models list endpoint."""
    if not router_engine:
        raise HTTPException(status_code=503, detail="Gateway initializing")
    models = router_engine.get_all_available_models()
    return {
        "object": "list",
        "data": models
    }


async def _dispatch_completion_body(body: Dict[str, Any]):
    is_streaming = body.get("stream", False)

    try:
        result, prov_id, model_id = await router_engine.execute_chat_completion(
            request_data=body,
            is_streaming=is_streaming
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "message": f"Gateway routing error: {str(e)}",
                    "type": "gateway_error",
                    "code": "upstream_failed"
                }
            }
        )

    if is_streaming:
        return StreamingResponse(
            result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Routed-Provider": prov_id,
                "X-Routed-Model": model_id,
            }
        )
    else:
        return JSONResponse(
            content=result,
            headers={
                "X-Routed-Provider": prov_id,
                "X-Routed-Model": model_id,
            }
        )


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(verify_unified_key)
):
    """
    OpenAI-compatible chat completions endpoint with dynamic auto-routing,
    per-model quota balancing, and failover.
    """
    if not router_engine:
        raise HTTPException(status_code=503, detail="Gateway initializing")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body in request")

    return await _dispatch_completion_body(body)


@app.post("/v1/completions")
async def legacy_completions(
    request: Request,
    token: str = Depends(verify_unified_key)
):
    """Adapts text completion requests to chat completions format."""
    if not router_engine:
        raise HTTPException(status_code=503, detail="Gateway initializing")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    prompt = body.get("prompt", "")
    messages = [{"role": "user", "content": prompt if isinstance(prompt, str) else str(prompt)}]
    
    chat_body = dict(body)
    chat_body["messages"] = messages
    if "prompt" in chat_body:
        del chat_body["prompt"]

    return await _dispatch_completion_body(chat_body)
