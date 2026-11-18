"""Session transaction boundary for the customer-support graph."""
from fastapi import HTTPException
from sqlalchemy import select, update
from app.models.entities import SupportSession, SupportMessage, SupportAudit
from app.schemas.contracts import SupportDecision
from app.agents.support import support_graph

async def history(db, session_id):
    if not await db.get(SupportSession, session_id):
        raise HTTPException(404, "Support session not found")
    rows = (await db.scalars(select(SupportMessage).where(
        SupportMessage.session_id == session_id).order_by(SupportMessage.id.desc()).limit(50))).all()
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]

async def audit_history(db, session_id):
    if not await db.get(SupportSession, session_id):
        raise HTTPException(404, "Support session not found")
    rows = (await db.scalars(select(SupportAudit).where(SupportAudit.session_id == session_id)
                           .order_by(SupportAudit.id.desc()).limit(50))).all()
    return [{"id": row.id, **row.data} for row in reversed(rows)]

async def locked_session(db, session_id):
    await db.execute(update(SupportSession).where(SupportSession.id == session_id).values(status=SupportSession.status))
    session = await db.get(SupportSession, session_id)
    if not session:
        raise HTTPException(404, "Support session not found")
    return session

async def record(db, session, reply, intent, decision, facts=None, citations=None):
    db.add(SupportMessage(session_id=session.id, role="assistant", content=reply))
    db.add(SupportAudit(session_id=session.id, data={"intent":intent,"decision":decision,
        "citations":citations or [],"needs_confirmation":bool(session.pending_action)}))
    await db.commit()
    return SupportDecision(reply=reply,intent=intent,needs_confirmation=bool(session.pending_action),
        session_status=session.status,facts=facts or [],citations=citations or [],decision=decision)

async def send_message(db, session_id, content, use_model=False):
    session = await locked_session(db, session_id)
    db.add(SupportMessage(session_id=session_id,role="user",content=content))
    result = await support_graph.ainvoke({"content":content,"pending":session.pending_action,
                                         "use_model":use_model,"trace":[]})
    if result.get("confirmation") in ("confirm","cancel"):
        return await _confirm(db,session,result["confirmation"]=="confirm",result["trace"])
    if result.get("pending_next"):
        session.pending_action=result["pending_next"]
        session.status="WAITING_CONFIRMATION"
    decision={"screening":result["screening"],"rule":result.get("rule"),"trace":result["trace"],"tools":result.get("tools",[])}
    return await record(db,session,result["reply"],result["intent"],decision,result.get("facts"),result.get("citations"))

async def _confirm(db, session, approved, trace=None):
    if session.status != "WAITING_CONFIRMATION" or not session.pending_action:
        raise HTTPException(409, "No action awaiting confirmation")
    pending=session.pending_action
    session.pending_action=None
    session.status="ESCALATED" if approved else "OPEN"
    if approved:
        reply="已记录人工审核申请（本地演示，无真实外部工单）。" if pending.get("type") in ("refund","refund_review") else "已记录人工处理诉求（本地演示，无真实外部工单）。"
    else:
        reply="已取消申请，您可以继续咨询。"
    return await record(db,session,reply,"refund" if pending.get("type") in ("refund","refund_review") else pending.get("type","request"),
        {"confirmation":"confirm" if approved else "cancel","rule":pending.get("decision"),"trace":trace or [{"node":"explicit_confirmation"}]})

async def confirm_action(db, session_id, approved):
    return await _confirm(db,await locked_session(db,session_id),approved)
