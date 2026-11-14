from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from app.agents.provider import draft


class MarketingState(TypedDict, total=False):
    brief: str
    audience: str
    budget: int
    variant: int
    title: str
    message: str
    reasoning: str
    score: int


async def planner(state: MarketingState):
    return {"reasoning": f"预算 {state['budget']}，目标客群 {state['audience']}"}


async def executor(state: MarketingState):
    return await draft(state["brief"], state["audience"], state["variant"])


async def critic(state: MarketingState):
    message = state["message"]
    if len(message) > 400:
        message = message[:397] + "..."
    if not message.strip():
        raise ValueError("Empty campaign content")
    # Deterministic rubric, not a claimed measured conversion uplift.
    score = max(0, min(100, 70 + min(len(message), 100) // 10 - 2 * (state["variant"] - 1)))
    return {"message": message, "score": score}


def build_graph():
    builder = StateGraph(MarketingState)
    builder.add_node("planner", planner)
    builder.add_node("executor", executor)
    builder.add_node("critic", critic)
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "executor")
    builder.add_edge("executor", "critic")
    builder.add_edge("critic", END)
    return builder.compile()


marketing_graph = build_graph()
