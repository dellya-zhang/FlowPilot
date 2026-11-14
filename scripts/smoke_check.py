"""Quick syntax and pure rules smoke check; does not require DB or LangGraph."""
from app.services.rules import evaluate, check_marketing

assert evaluate({"field":"amount", "op":"gte", "value":100}, {"amount":100})
assert not evaluate({"field":"missing", "op":"eq", "value":1}, {})
assert check_marketing("保证收益", 200_000)
print("Rules smoke checks OK")
