from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping

from .contracts import HarvestCandidate, SearchPlan


class KBHarvester:
    def make_candidate(self, *, result: Mapping[str, object], plan: SearchPlan) -> HarvestCandidate:
        statement = str(result.get("statement") or "").strip()
        source_uri = str(result.get("source_uri") or "").strip()
        if not statement or not source_uri:
            raise ValueError("harvest_statement_and_source_required")
        return HarvestCandidate(
            statement=statement,
            source_uri=source_uri,
            issuer=str(result.get("issuer")) if result.get("issuer") else None,
            retrieved_at=str(result.get("retrieved_at") or datetime.now(UTC).isoformat()),
            published_updated_effective_date=str(result.get("published_updated_effective_date")) if result.get("published_updated_effective_date") else None,
            supported_scope=[str(x) for x in result.get("supported_scope", plan.targets)],
            conditions=[str(x) for x in result.get("conditions", [])],
            exceptions=[str(x) for x in result.get("exceptions", [])],
            counter_evidence=[str(x) for x in result.get("counter_evidence", [])],
            retrieval_status="candidate",
            unresolved_gaps=list(plan.unresolved_gaps),
            canonical=False,
        )
