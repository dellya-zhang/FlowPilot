"""Local deterministic screening and priority rules; no external write tools."""
from app.services.rules import evaluate

RULES = (
    {"id": "complaint_handoff", "priority": 10, "intent": "complaint", "action": "handoff"},
    {"id": "shipping_handoff", "priority": 20, "intent": "shipping_request", "action": "handoff"},
    {"id": "refund_confirmation", "priority": 30, "intent": "refund", "action": "confirm_with_user"},
)


def screen(content, pending=None):
    text = content.strip().lower()
    result = {"branch": "query", "intent": "faq", "confirmation": None, "ambiguity": None, "entities": {}}
    if pending:
        result.update(branch="confirmation", intent="confirmation", confirmation="unclear")
        if text in ("确认", "好的", "是", "好", "confirm", "可以", "ok"):
            result["confirmation"] = "confirm"
        elif text in ("取消", "不", "cancel", "不要", "算了", "不用了"):
            result["confirmation"] = "cancel"
        return result
    if text in ("我想退", "帮我处理", "这个怎么办"):
        result.update(intent="clarify", ambiguity="请说明要处理的对象：订单退款、会员咨询，还是物流查询？")
        return result
    if any(w in text for w in ("不退款", "不要退款", "不想退款", "不用退款", "不需要退款", "不申请退款", "do not refund", "don't refund", "no refund")):
        result.update(intent="clarify", ambiguity="未提交退款审核申请。如需查询政策或订单，请说明具体问题。")
        return result
    query = any(w in text for w in ("政策", "规则", "条件", "是什么", "怎么", "如何", "能否", "可以吗", "policy", "how", "can i"))
    action = any(w in text for w in ("我要", "我想", "申请", "帮我", "给我", "please refund", "want a refund", "request a refund"))
    if not query and action:
        for words, intent in [(("退款", "refund", "退钱"), "refund"), (("投诉",), "complaint"), (("催", "物流"), "shipping_request")]:
            if any(w in text for w in words) and (intent != "shipping_request" or "催" in text):
                result.update(branch="complex", intent=intent)
                return result
        if any(w in text for w in ("查", "看", "check", "look up")):
            if "订单" in text or "order" in text:
                result["intent"] = "order_lookup"
                return result
            if any(w in text for w in ("积分", "会员", "物流", "points", "shipping")):
                return result
        result.update(branch="complex", intent="unknown_request")
    elif any(w in text for w in ("订单", "order")) and not any(w in text for w in ("退款", "refund")):
        result["intent"] = "order_lookup"
    return result


def decide(turn, rules=RULES):
    for rule in sorted(rules, key=lambda r: r["priority"]):
        if rule["intent"] not in ("*", turn["intent"]):
            continue
        if not all(evaluate(condition, turn["entities"]) for condition in rule.get("conditions", [])):
            continue
        return {"rule_id": rule["id"], "action": rule["action"], "fallback": False}
    return {"rule_id": None, "action": "confirm_with_user", "fallback": True}
