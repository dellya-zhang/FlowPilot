"""Deterministic support orchestration with evidence-bearing read tools.
No LLM is allowed to authorize a business write.
"""
from fastapi import HTTPException
from sqlalchemy import select, update
from app.models.entities import SupportSession, SupportMessage
from app.schemas.contracts import SupportDecision
from app.services.support_policy import screen, decide

FAQ = [
    ("退款", "refund_policy", "退款需提交订单信息并由人工审核，本演示不执行资金操作。"),
    ("会员", "membership_policy", "会员权益以活动说明为准；本演示不售卖会员。"),
    ("物流", "shipping_policy", "物流状态需以订单系统返回为准；本演示未接入真实物流。"),
]


async def history(db, session_id):
    if not await db.get(SupportSession, session_id):
        raise HTTPException(404, "Support session not found")
    rows = (await db.scalars(select(SupportMessage).where(
        SupportMessage.session_id == session_id).order_by(SupportMessage.id.desc()).limit(50))).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


async def send_message(db, session_id: str, content: str) -> SupportDecision:
    # Serialize a conversation turn, including confirmations, in the database.
    await db.execute(update(SupportSession).where(SupportSession.id == session_id).values(
        status=SupportSession.status))
    session = await db.get(SupportSession, session_id)
    if not session:
        raise HTTPException(404, "Support session not found")
    db.add(SupportMessage(session_id=session_id, role="user", content=content))
    turn = screen(content, session.pending_action)
    facts = []
    decision = None
    intent = turn["intent"]
    if turn["branch"] == "confirmation":
        if turn["confirmation"] in ("confirm", "cancel"):
            return await _confirm(db, session, turn["confirmation"] == "confirm")
        reply = "还有待确认的申请，请确认或取消。"
    elif turn["ambiguity"]:
        reply = turn["ambiguity"]
    elif turn["branch"] == "complex":
        decision = decide(turn)
        action = decision["action"]
        if action == "handoff":
            intent, reply = "handoff", "建议联系人工客服处理；当前未连接人工客服系统。"
        elif action == "reject":
            intent, reply = "reject", "无法执行该操作。"
        else:
            session.status = "WAITING_CONFIRMATION"
            session.pending_action = {"type": intent, "requested_text": content[:200], "decision": decision}
            reply = "是否确认提交退款人工审核申请？本演示不执行真实退款。" if intent == "refund" else "是否确认记录该诉求？本演示不会执行外部操作。"
    elif intent == "order_lookup":
        facts = [{"source": "demo_order_tool", "fact": "当前未连接真实订单服务，无法查询个人订单。"}]
        reply = facts[0]["fact"]
    else:
        text = content.strip().lower()
        facts = [{"source": source, "fact": answer} for keyword, source, answer in FAQ if keyword in text]
        reply = "\n".join(f["fact"] for f in facts) or "请描述具体问题，我可以协助政策咨询、订单查询与退款审核申请。"
    db.add(SupportMessage(session_id=session_id, role="assistant", content=reply))
    await db.commit()
    return SupportDecision(reply=reply, intent=intent, needs_confirmation=bool(session.pending_action),
                           session_status=session.status, facts=facts, decision={"screening": turn, "rule": decision})


async def _confirm(db, session, approved):
    if session.status != "WAITING_CONFIRMATION" or not session.pending_action:
        raise HTTPException(409, "No action awaiting confirmation")
    pending = session.pending_action
    session.pending_action = None
    session.status = "ESCALATED" if approved else "OPEN"
    reply = "已记录人工审核申请（本地演示，无真实外部工单）。" if approved else "已取消申请，您可以继续咨询。"
    db.add(SupportMessage(session_id=session.id, role="assistant", content=reply))
    await db.commit()
    return SupportDecision(reply=reply, intent="refund" if pending.get("type") in ("refund", "refund_review") else pending.get("type", "request"), needs_confirmation=False, session_status=session.status, decision={"confirmation": "confirm" if approved else "cancel", "rule": pending.get("decision")})


async def confirm_action(db, session_id: str, approved: bool) -> SupportDecision:
    await db.execute(update(SupportSession).where(SupportSession.id == session_id).values(
        status=SupportSession.status))
    session = await db.get(SupportSession, session_id)
    if not session:
        raise HTTPException(404, "Support session not found")
    return await _confirm(db, session, approved)
