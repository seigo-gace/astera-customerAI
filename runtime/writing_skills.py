from __future__ import annotations

import re

from .contracts import SkillDescriptor, SkillValidationState


A = SkillValidationState.ACTIVE


def default_writing_skills() -> list[SkillDescriptor]:
    raw = [
        ("ja-writing-preset", "Japanese Writing Preset", ["ja"], ["clarity", "structure"], [], "日本語として自然で読みやすく、結論と条件を明確に書く。", True),
        ("ja-jtf-style", "Japanese JTF Style Preset", ["ja"], ["style", "consistency"], [], "JTF系の表記一貫性を意識し、表記揺れを増やさない。", False),
        ("ja-technical-writing", "Japanese Technical Writing", ["ja"], ["technical", "procedure"], ["procedure", "troubleshooting"], "技術説明は前提・手順・結果・例外を分離する。", False),
        ("ja-textlint", "Japanese Textlint Layer", ["ja"], ["lint", "clarity"], [], "冗長・重複・曖昧な係り受けを避ける。", False),
        ("ja-proofreader", "Japanese Proofreader", ["ja"], ["proofread", "grammar"], [], "誤字、助詞、語尾、論理接続を最終確認する。", True),
        ("ja-review-gate", "Japanese Review Gate", ["ja"], ["review", "quality"], [], "不足、矛盾、過剰断定を回答前に検査する。", False),
        ("ja-style-normalizer", "Japanese Style Normalizer", ["ja"], ["style", "normalization"], [], "文体と見出し粒度を一貫させる。", True),
        ("ja-technical-checker", "Japanese Technical Writing Checker", ["ja"], ["technical", "review"], ["procedure", "troubleshooting", "comparison"], "技術語、条件、依存関係、手順順序の欠落を検査する。", False),
        ("ja-terminology", "Japanese Terminology Normalizer", ["ja"], ["terminology"], [], "承認済み用語を優先し、勝手な別名を作らない。", True),
        ("writing-editing", "Writing / Editing Skill", ["ja", "en"], ["editing", "clarity"], [], "意味を変えずに構造、可読性、重複を改善する。", False),
        ("writing-plans", "Writing Plans", ["ja", "en"], ["planning", "structure"], [], "長文は先に回答構造を決め、同じ内容を繰り返さない。", False),
        ("knowledge-extractor", "Knowledge Extractor", ["ja", "en"], ["evidence", "knowledge"], [], "Evidenceの事実・条件・例外を落とさず文章へ反映する。", False),
        ("skill-composer", "Skill Composer", ["ja", "en"], ["composition"], [], "選択された能力だけを衝突なく統合する。", False),
        ("en-grammar", "English Grammar & Syntax Guard", ["en"], ["grammar", "syntax"], [], "Use correct, direct grammar and unambiguous sentence structure.", True),
        ("en-plain-clear", "English Plain & Clear Writing", ["en"], ["clarity", "plain_language"], [], "Prefer plain, direct English without losing required detail.", True),
        ("en-technical", "English Technical Writing", ["en"], ["technical", "procedure"], ["procedure", "troubleshooting"], "Separate prerequisites, steps, results, and exceptions.", False),
        ("en-terminology", "English Terminology Consistency", ["en"], ["terminology"], [], "Keep product and technical terms consistent with supplied evidence.", True),
        ("en-evidence", "English Evidence-bound Writing", ["en"], ["evidence", "claims"], [], "Do not state unsupported product facts; preserve conditions and exceptions.", False),
        ("en-concision", "English Concision & Redundancy Guard", ["en"], ["concision", "editing"], [], "Remove repetition while keeping necessary conditions and actions.", False),
        ("en-audience", "English Audience & Tone Adapter", ["en"], ["audience", "tone"], [], "Match depth and terminology to the requested audience.", False),
        ("en-proofreader", "English Final Proofreader", ["en"], ["proofread", "quality"], [], "Perform a final grammar, consistency, and clarity pass.", True),
    ]
    return [SkillDescriptor(skill_id=i, name=n, languages=l, capabilities=c, task_shapes=s, capsule=p, state=A, baseline=b) for i,n,l,c,s,p,b in raw]


class WritingRefiner:
    _MULTI_NL = re.compile(r"\n{3,}")

    def refine(self, text: str) -> str:
        # Model再呼出しを増やさず、意味を変えない安全な最終整形だけ行う。
        paragraphs = []
        seen = set()
        for part in self._MULTI_NL.sub("\n\n", text.strip()).split("\n\n"):
            clean = part.strip()
            if clean and clean not in seen:
                seen.add(clean)
                paragraphs.append(clean)
        return "\n\n".join(paragraphs)
