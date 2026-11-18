"""Delivery graph shared by pilot and full. Freeze approved copy, never overwrite
operator edits with a model. Publication happens only after the terminal guard."""
from typing import TypedDict
import csv
import io
import hashlib
from langgraph.graph import StateGraph, START, END
from fastapi import HTTPException
from app.services.rules import check_marketing
from app.services.versions import digest

class DeliveryState(TypedDict, total=False):
    binding: dict
    customers: list
    budget: int
    kind: str
    trace: list[str]
    content: str
    blob: bytes
    sha256: str

def snapshot(s):
    binding=s["binding"]
    data=binding["snapshot"]
    if digest(data)!=binding["snapshot_sha256"]:
        raise HTTPException(409,"Approved snapshot integrity check failed")
    return {"trace":["load_snapshot"]}

def audience(s):
    rows=s["customers"]
    if len({c.id for c in rows})!=len(rows) or any(not c.consent for c in rows):
        raise HTTPException(422,"Audience contains duplicates or withdrawn consent")
    return {"trace":s["trace"]+["validate_audience"]}

def content(s):
    # Formal payload is built from the approved version, not a second creative model call.
    return {"content":s["binding"]["snapshot"]["message"],"trace":s["trace"]+["materialize_approved_content"]}

def compliance(s):
    data=s["binding"]["snapshot"]; plan=data.get("plan_data") or {}
    strategy=plan.get("strategy") or {}; estimate=plan.get("cost_estimate") or {}
    issues=check_marketing(data["title"]+"\n"+s["content"],s["budget"])
    if (estimate.get("unit_cost_cny") or 0)*strategy.get("contacts_per_user",1)*len(s["customers"])>s["budget"]:
        issues.append("delivery_budget_exceeded")
    if not s["content"].strip():issues.append("empty_content")
    if issues:raise HTTPException(422,{"message":"Delivery blocked by compliance","issues":issues})
    return {"trace":s["trace"]+["terminal_compliance"]}

def generate_csv(rows,binding=None):
    stream=io.StringIO(newline="")
    writer=csv.writer(stream)
    writer.writerow(["customer_id","segment","region"]+(["candidate_id","plan_revision","channel","contacts_per_user","message"] if binding else []))
    for row in rows:
        values=[row.id,row.segment,row.region]
        if binding:
            data=binding["snapshot"]; strategy=(data.get("plan_data") or {}).get("strategy",{})
            message=data["message"]
            if message.lstrip().startswith(("=","+","-","@")):message="'"+message
            values += [binding["candidate_id"],binding["revision"],strategy.get("channel","manual"),strategy.get("contacts_per_user",1),message]
        writer.writerow(values)
    return stream.getvalue().encode("utf-8-sig")

def assemble(s):
    blob=generate_csv(s["customers"],s["binding"])
    return {"blob":blob,"sha256":hashlib.sha256(blob).hexdigest(),"trace":s["trace"]+["assemble_csv"]}

def build():
    graph=StateGraph(DeliveryState)
    previous=START
    for name, node in [("load_snapshot",snapshot),("validate_audience",audience),("materialize_content",content),
                       ("terminal_compliance",compliance),("assemble_csv",assemble)]:
        graph.add_node(name,node);graph.add_edge(previous,name);previous=name
    graph.add_edge(previous,END)
    return graph.compile()

delivery_graph=build()
