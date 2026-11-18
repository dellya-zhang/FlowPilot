from typing import Any
import re

OPS = {
    "eq": lambda a,b: a == b,
    "ne": lambda a,b: a != b,
    "gt": lambda a,b: a > b,
    "gte": lambda a,b: a >= b,
    "lt": lambda a,b: a < b,
    "lte": lambda a,b: a <= b,
    "in": lambda a,b: a in b,
}


def evaluate(rule: dict, context: dict[str, Any]) -> bool:
    """Allowlisted 7-op DSL. Never eval untrusted Python expressions."""
    if not isinstance(rule, dict) or not isinstance(context, dict):
        return False
    field, operator, value = rule.get("field"), rule.get("op"), rule.get("value")
    if not isinstance(operator, str) or not isinstance(field, str) or operator not in OPS or field not in context:
        return False
    try:
        return bool(OPS[operator](context[field], value))
    except (TypeError, ValueError):
        return False


def check_marketing(message: str, budget: int) -> list[str]:
    issues = []
    if budget > 100_000:
        issues.append("budget_exceeds_demo_policy")
    for token in ("保证收益", "百分百中奖", "无需同意"):
        if token in message:
            issues.append("prohibited_claim")
    if re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?!\w)", message):
        issues.append("possible_personal_information")
    return issues
