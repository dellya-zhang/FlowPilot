"""Deterministic support orchestration with evidence-bearing read tools.
No LLM is allowed to authorize a business write.
"""
from fastapi import HTTPException
from sqlalchemy import select, update
from app.models.entities import SupportSession, SupportMessage
from app.schemas.contracts import SupportDecision

FAQ = [
    ("退款", "refund_policy", "退款需提交订单信息并由人工审核，本演示不执行资金操作。"),
    ("会员", "membership_policy", "会员权益以活动说明为准；本演示不售卖会员。"),
    ("物流", "shipping_policy", "物流状态需以订单系统返回为准；本演示未接入真实物流。"),
]


async def history(db, session_id):
    if not await db.get(SupportSession, session_id):
        raise HTTPException(404, "Support session not found")
    rows = (await db.scalars(select(SupportMessage).where(
        SupportMessage.session_id == session_id).order_by(SupportMessage.id.desc()).limit(200))).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


async def send_message(db, session_id: str, content: str) -> SupportDecision:
    # Serialize a conversation turn, including confirmations, in the database.
    await db.execute(update(SupportSession).where(SupportSession.id == session_id).values(
        status=SupportSession.status))
    session = await db.get(SupportSession, session_id)
    if not session:
        raise HTTPException(404, "Support session not found")
    db.add(SupportMessage(session_id=session_id, role="user", content=content))
    text = content.strip().lower()
    facts = []
    if session.pending_action and text in ("确认", "好的", "是", "好", "confirm", "取消", "不", "cancel"):
        return await _confirm(db, session, text not in ("取消", "不", "cancel"))
    if session.pending_action:
        intent, reply = "confirmation", "存在待确认的退款审核申请，请先确认或取消，避免覆盖待执行操作。"
    elif text in ("我想退", "帮我处理", "这个怎么办"):
        intent, reply = "clarify", "请说明要处理的对象：订单退款、会员咨询，还是物流查询？"
    elif any(w in text for w in ("退款", "refund", "退钱")) and not any(w in text for w in ("政策", "规则", "条件", "是什么", "policy")):
        intent = "refund"
        session.status = "WAITING_CONFIRMATION"
        session.pending_action = {"type": "refund_review", "requested_text": content[:200]}
        reply = "是否确认提交退款人工审核申请？本演示不执行真实退款。"
    elif any(w in text for w in ("订单", "order")):
        intent = "order_lookup"
        facts = [{"source": "demo_order_tool", "fact": "当前未连接真实订单服务，无法查询个人订单。"}]
        reply = facts[0]["fact"]
    else:
        intent = "faq"
        facts = [{"source": source, "fact": answer} for keyword, source, answer in FAQ if keyword in text]
        reply = "\n".join(f["fact"] for f in facts) or "我可以协助订单查询、退款审核申请和会员政策咨询，请描述具体问题。"
    db.add(SupportMessage(session_id=session_id, role="assistant", content=reply))
    await db.commit()
    return SupportDecision(reply=reply, intent=intent, needs_confirmation=bool(session.pending_action),
                           session_status=session.status, facts=facts)


async def _confirm(db, session, approved):
    if session.status != "WAITING_CONFIRMATION" or not session.pending_action:
        raise HTTPException(409, "No action awaiting confirmation")
    session.pending_action = None
    session.status = "ESCALATED" if approved else "OPEN"
    reply = "已记录人工审核申请（本地演示，无真实外部工单）。" if approved else "已取消申请，您可以继续咨询。"
    db.add(SupportMessage(session_id=session.id, role="assistant", content=reply))
    await db.commit()
    return SupportDecision(reply=reply, intent="refund", needs_confirmation=False, session_status=session.status)


async def confirm_action(db, session_id: str, approved: bool) -> SupportDecision:
    await db.execute(update(SupportSession).where(SupportSession.id == session_id).values(
        status=SupportSession.status))
    session = await db.get(SupportSession, session_id)
    if not session:
        raise HTTPException(404, "Support session not found")
    return await _confirm(db, session, approved)
