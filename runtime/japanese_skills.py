from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from rapidfuzz import fuzz

_ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._+-]*", re.IGNORECASE)
_ASCII_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]*$", re.IGNORECASE)
_WS_RE = re.compile(r"[\t\u00a0\u3000 ]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")

@dataclass(frozen=True)
class NormalizedJapaneseText:
    raw: str
    normalized: str

class JapaneseSurfaceNormalizer:
    def normalize(self, text: str) -> NormalizedJapaneseText:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        normalized = "\n".join(_WS_RE.sub(" ", line).strip() for line in normalized.split("\n"))
        normalized = _MULTI_NL_RE.sub("\n\n", normalized).strip()
        return NormalizedJapaneseText(raw=text, normalized=normalized)

@dataclass(frozen=True)
class TermCandidate:
    canonical: str
    matched_alias: str
    score: float
    exact: bool

class AsteraTermAliasSkill:
    def __init__(self, alias_registry: Mapping[str, Iterable[str]], normalizer: JapaneseSurfaceNormalizer | None = None):
        self.normalizer = normalizer or JapaneseSurfaceNormalizer(); self._aliases=[]
        for canonical, aliases in alias_registry.items():
            for alias in {canonical, *aliases}:
                norm=self.normalizer.normalize(alias).normalized.casefold()
                if norm: self._aliases.append((canonical, alias, norm))
    @staticmethod
    def _ascii_exact(alias: str, query: str) -> bool:
        return bool(re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.IGNORECASE).search(query))
    @staticmethod
    def _ascii_fuzzy_score(alias: str, query: str) -> float:
        scores=[]
        for token in _ASCII_TOKEN_RE.findall(query):
            token=token.casefold()
            if abs(len(token)-len(alias)) > max(1, len(alias)//3): continue
            scores.append(float(fuzz.ratio(alias, token)))
        return max(scores, default=0.0)
    def candidates(self, text: str, *, fuzzy_threshold: float) -> list[TermCandidate]:
        query=self.normalizer.normalize(text).normalized.casefold(); found={}
        for canonical, alias, norm_alias in self._aliases:
            is_ascii=bool(_ASCII_ALIAS_RE.fullmatch(norm_alias))
            if is_ascii:
                exact=self._ascii_exact(norm_alias, query); score=100.0 if exact else self._ascii_fuzzy_score(norm_alias, query)
            else:
                exact=norm_alias in query; score=100.0 if exact else float(fuzz.partial_ratio(norm_alias, query)) if len(norm_alias)>=4 else 0.0
            if exact or score>=fuzzy_threshold:
                candidate=TermCandidate(canonical, alias, score, exact); previous=found.get(canonical)
                if previous is None or (candidate.exact, candidate.score)>(previous.exact, previous.score): found[canonical]=candidate
        return sorted(found.values(), key=lambda x:(not x.exact,-x.score,x.canonical))

@dataclass(frozen=True)
class ConversationContext:
    active_topics: tuple[str,...]=()
    last_user_need: str=""
    user_conditions: tuple[tuple[str,str],...]=()

class JapaneseEllipsisContextSkill:
    SHORT_FOLLOWUP_MARKERS=("それは","それで","じゃあ","では","なら","こっちは","これは","あれは","あと","で、")
    def bind(self,text:str,context:ConversationContext)->dict[str,object]:
        normalized=JapaneseSurfaceNormalizer().normalize(text).normalized; is_short=len(normalized)<=40; marker_hit=any(normalized.startswith(m) for m in self.SHORT_FOLLOWUP_MARKERS)
        return {"text":normalized,"is_ellipsis_followup":bool(is_short and marker_hit and (context.active_topics or context.last_user_need)),"active_topics":list(context.active_topics),"last_user_need":context.last_user_need,"user_conditions":dict(context.user_conditions)}

@dataclass(frozen=True)
class TerminologyViolation:
    alias:str
    canonical:str

class JapaneseResponseTerminologyGuard:
    def __init__(self,alias_registry:Mapping[str,Iterable[str]]):
        self._pairs=[(alias,canonical) for canonical,aliases in alias_registry.items() for alias in aliases if alias!=canonical]
    @staticmethod
    def _contains_term(text:str,term:str)->bool:
        nt=unicodedata.normalize("NFKC",text); nm=unicodedata.normalize("NFKC",term)
        if _ASCII_ALIAS_RE.fullmatch(nm): return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(nm)}(?![A-Za-z0-9])",nt))
        return nm in nt
    def check(self,answer:str)->list[TerminologyViolation]:
        return [TerminologyViolation(alias=a,canonical=c) for a,c in self._pairs if self._contains_term(answer,a) and not self._contains_term(answer,c)]

class JapaneseShortQASkillPack:
    def __init__(self,*,alias_registry:Mapping[str,Iterable[str]],fuzzy_threshold:float):
        self.normalizer=JapaneseSurfaceNormalizer(); self.alias=AsteraTermAliasSkill(alias_registry,self.normalizer); self.ellipsis=JapaneseEllipsisContextSkill(); self.terminology=JapaneseResponseTerminologyGuard(alias_registry); self.fuzzy_threshold=fuzzy_threshold
    def prepare(self,text:str,context:ConversationContext)->dict[str,object]:
        normalized=self.normalizer.normalize(text); aliases=self.alias.candidates(normalized.normalized,fuzzy_threshold=self.fuzzy_threshold); ellipsis=self.ellipsis.bind(normalized.normalized,context)
        return {"raw_text":normalized.raw,"normalized_text":normalized.normalized,"term_candidates":[candidate.__dict__ for candidate in aliases],"ellipsis":ellipsis}
    def terminology_violations(self,answer:str)->list[TerminologyViolation]: return self.terminology.check(answer)
