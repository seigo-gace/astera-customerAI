from evaluation.release import ReleaseGateConfig,evaluate_release
from evaluation.satisfaction import wilson_lower_bound
from evaluation.scorer import ScenarioScore
def build_scores(count=220):
    classes=[f"c{i}" for i in range(11)]; return [ScenarioScore(scenario_id=f"s{i}",scenario_class=classes[i%11],critical=i<35,multi_turn=35<=i<60,false_premise=60<=i<85,resolved=True,satisfied=True,false_premise_corrected=True) for i in range(count)]
def test_200_of_200_wilson_lower_bound_exceeds_98_percent(): assert wilson_lower_bound(200,200)>.98
def test_36_of_36_is_not_release_evidence():
    d=evaluate_release(build_scores(36),ReleaseGateConfig(min_each_class=1,min_critical=1,min_multiturn=0,min_false_premise=0)); assert not d.passed and "insufficient_unseen_scenarios" in d.failures
def test_sufficient_all_pass_corpus_can_pass_release_gate(): assert evaluate_release(build_scores(220)).passed
