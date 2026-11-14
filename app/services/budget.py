"""Same-host, cross-process budget journal. Never store credentials or prompts."""
import json
import os
import tempfile
import uuid
from pathlib import Path
from filelock import FileLock


class BudgetExceeded(ValueError):
    pass


def _append(path, rows, event):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            for row in [*rows,event]:
                out.write(json.dumps(row,ensure_ascii=False)+'\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def reserve_call(path, reserve_cny, model, max_calls, budget_cny):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with FileLock(str(path)+'.lock',timeout=10):
        rows=[json.loads(x) for x in path.read_text('utf-8').splitlines()] if path.exists() else []
        reservations={r['call_id']:r['reserve_cny'] for r in rows if r['event']=='reserved'}
        settled={r['call_id']:r['charged_upper_cny'] for r in rows if r['event']=='settled'}
        spent=sum(settled.get(k,v) for k,v in reservations.items())
        if len(reservations)>=max_calls or spent+reserve_cny>budget_cny:
            raise BudgetExceeded('Model request or spending cap reached')
        call_id=uuid.uuid4().hex
        _append(path,rows,{'event':'reserved','call_id':call_id,'reserve_cny':reserve_cny,'model':model})
        return call_id


def settle_call(path,event):
    path=Path(path)
    with FileLock(str(path)+'.lock',timeout=10):
        rows=[json.loads(x) for x in path.read_text('utf-8').splitlines()]
        # Idempotent settlement; unknown reservation is not silently inserted.
        if not any(r['event']=='reserved' and r['call_id']==event['call_id'] for r in rows):
            raise ValueError('Missing budget reservation')
        if any(r['event']=='settled' and r['call_id']==event['call_id'] for r in rows):return
        _append(path,rows,event)
