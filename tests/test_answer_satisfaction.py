from runtime.answer_quality import RuntimeSatisfactionGate,RuntimeSatisfactionSignals
from runtime.schemas import ResolutionMode
def good(): return RuntimeSatisfactionSignals(True,True,True,True,True)
def test_resolved_good_signals_pass():
    p,f=RuntimeSatisfactionGate().evaluate(ResolutionMode.RESOLVED,good()); assert p and f==[]
def test_partial_never_counts_as_satisfaction_success():
    p,f=RuntimeSatisfactionGate().evaluate(ResolutionMode.SAFE_PARTIAL,good()); assert not p and "conversation_not_resolved" in f
