"""Read-only knowledge answers; no business mutations or model-triggered actions."""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from pydantic import Field
from app.schemas.contracts import InputModel
from app.services.knowledge import answer, search
from app.services.limiter import limit
from app.services.budget import BudgetExceeded

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class KnowledgeQuery(InputModel):
    question: str = Field(min_length=2, max_length=500)
    use_model: bool = False


@router.post("/search")
async def search_knowledge(data: KnowledgeQuery):
    return {"results": search(data.question)}


@router.post("/answer")
async def answer_knowledge(data: KnowledgeQuery, request: Request):
    await limit(request, "knowledge", 20)
    if not data.use_model:
        return await answer(data.question)
    slots = request.app.state.model_slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.2)
    except TimeoutError:
        raise HTTPException(429, "Model capacity reached", headers={"Retry-After":"2"})
    try:
        return await answer(data.question, use_model=True)
    except BudgetExceeded:
        raise HTTPException(429, "Model budget reached")
    except Exception as exc:
        logging.getLogger(__name__).warning("Knowledge model failed: %s", type(exc).__name__)
        raise HTTPException(502, "Knowledge model unavailable; retry without model selection")
    finally:
        slots.release()
