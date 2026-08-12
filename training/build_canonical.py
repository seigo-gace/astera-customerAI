from __future__ import annotations
from collections import defaultdict
from .schemas import CanonicalFact, RawFact
class CanonicalConflictError(ValueError): pass
def build_canonical(raw_facts:list[RawFact])->list[CanonicalFact]:
    approved=[i for i in raw_facts if i.status=="approved" and i.public]; grouped=defaultdict(list)
    for i in approved: grouped[i.fact_id].append(i)
    out=[]
    for fact_id,items in grouped.items():
        statements={i.statement.strip() for i in items}
        if len(statements)>1: raise CanonicalConflictError(f"conflicting approved facts: {fact_id}")
        c=items[-1]; out.append(CanonicalFact(fact_id=c.fact_id,topic=c.topic,statement=c.statement,source_id=c.source_id,public=c.public,volatile=c.volatile))
    return sorted(out,key=lambda x:x.fact_id)
