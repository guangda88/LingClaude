from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from lingclaude.self_optimizer.learner.models import (
    FeedbackCategory,
    FeedbackItem,
    LearnedRule,
    Pattern,
)


class RuleExtractor:
    def __init__(
        self,
        min_frequency: int = 3,
        min_confidence: float = 0.7,
        max_rules: int = 1000,
    ) -> None:
        self.min_frequency = min_frequency
        self.min_confidence = min_confidence
        self.max_rules = max_rules
        self._extracted_count = 0

    def extract_rules(
        self,
        feedback_items: list[FeedbackItem],
        category: FeedbackCategory | None = None,
    ) -> tuple[LearnedRule, ...]:
        if category:
            feedback_items = [
                item for item in feedback_items if item.category == category
            ]

        rule_groups: dict[str, list[FeedbackItem]] = defaultdict(list)
        for item in feedback_items:
            rule_id = self._normalize_rule_id(item.rule_id)
            rule_groups[rule_id].append(item)

        learned_rules: list[LearnedRule] = []
        for rule_id, items in rule_groups.items():
            if len(items) >= self.min_frequency:
                rule = self._extract_single_rule(rule_id, items)
                if rule and rule.confidence >= self.min_confidence:
                    learned_rules.append(rule)
                    self._extracted_count += 1
                    if self._extracted_count >= self.max_rules:
                        break

        learned_rules.sort(key=lambda r: r.quality_score, reverse=True)
        return tuple(learned_rules)

    def _normalize_rule_id(self, rule_id: str) -> str:
        rule_id = re.sub(r"[^\w.-]", "_", rule_id)
        return rule_id.lower().strip()

    def _extract_single_rule(
        self, rule_id: str, items: list[FeedbackItem]
    ) -> LearnedRule | None:
        try:
            tool_names = list(set(item.tool_name for item in items))
            avg_confidence = sum(item.confidence for item in items) / len(items)

            category_counts = Counter(item.category for item in items)
            primary_category = category_counts.most_common(1)[0][0]

            pattern = self._build_pattern(items)

            rule = LearnedRule(
                id=rule_id,
                name=self._generate_rule_name(rule_id, items),
                description=self._generate_description(items),
                category=primary_category,
                pattern=pattern,
                tools=tuple(tool_names),
                frequency=len(items),
                confidence=round(avg_confidence, 2),
            )

            score = self._calculate_quality_score(rule)
            return LearnedRule(
                id=rule.id,
                name=rule.name,
                description=rule.description,
                category=rule.category,
                pattern=rule.pattern,
                tools=rule.tools,
                frequency=rule.frequency,
                confidence=rule.confidence,
                quality_score=score,
                created_at=rule.created_at,
            )
        except Exception:
            return None

    def _build_pattern(self, items: list[FeedbackItem]) -> Pattern:
        file_extensions: set[str] = set()
        for item in items:
            if "." in item.file_path:
                ext = item.file_path.split(".")[-1]
                file_extensions.add(f"*.{ext}")

        seen_patterns: set[str] = set()
        for item in items:
            if item.snippet:
                normalized = re.sub(r"\s+", " ", item.snippet.strip())
                seen_patterns.add(normalized)

        context_keywords: set[str] = set()
        for item in items:
            words = re.findall(r"\b[a-zA-Z_]{4,}\b", item.message)
            context_keywords.update(words[:5])

        severity_dist: dict[str, int] = {}
        for item in items:
            sev = item.severity.value
            severity_dist[sev] = severity_dist.get(sev, 0) + 1

        return Pattern(
            file_patterns=tuple(sorted(file_extensions)),
            code_patterns=tuple(sorted(seen_patterns)),
            context_keywords=tuple(sorted(context_keywords)),
            severity_distribution=severity_dist,
        )

    def _generate_rule_name(self, rule_id: str, items: list[FeedbackItem]) -> str:
        base_message = items[0].message
        name = base_message.replace("this ", "").replace("the ", "")
        name = re.sub(r"[^\w\s-]", "", name).strip().title()
        return name[:60]

    def _generate_description(self, items: list[FeedbackItem]) -> str:
        base_desc = items[0].message
        tool_names = list(set(item.tool_name for item in items))
        occ = f"{len(items)} occurrence{'s' if len(items) > 1 else ''}"
        return f"{base_desc} (Detected by {len(tool_names)} tool{'s' if len(tool_names) > 1 else ''}, {occ})"

    def _calculate_quality_score(self, rule: LearnedRule) -> float:
        diversity_score = min(len(set(rule.tools)) * 0.15, 0.3)
        frequency_score = min(rule.frequency / 10.0, 0.3)
        confidence_score = rule.confidence * 0.2
        if len(rule.pattern.file_patterns) == 1:
            specificity_score = 0.2
        elif len(rule.pattern.file_patterns) > 3:
            specificity_score = 0.05
        else:
            specificity_score = len(rule.pattern.file_patterns) * 0.07
        return min(diversity_score + frequency_score + confidence_score + specificity_score, 1.0)


class SecurityRuleExtractor(RuleExtractor):
    def extract_rules(
        self,
        feedback_items: list[FeedbackItem],
        category: FeedbackCategory | None = None,
    ) -> tuple[LearnedRule, ...]:
        if category is None:
            category = FeedbackCategory.SECURITY
        return super().extract_rules(feedback_items, category)


class RuleDeduplicator:
    def __init__(self, similarity_threshold: float = 0.8) -> None:
        self.similarity_threshold = similarity_threshold

    def deduplicate(
        self, rules: list[LearnedRule]
    ) -> tuple[LearnedRule, ...]:
        if not rules:
            return ()

        unique_rules: list[LearnedRule] = []
        seen_hashes: set[str] = set()

        for rule in rules:
            rule_hash = self._compute_rule_hash(rule)
            is_duplicate = False
            for existing_hash in seen_hashes:
                if self._are_similar(rule_hash, existing_hash):
                    is_duplicate = True
                    break

            if not is_duplicate:
                unique_rules.append(rule)
                seen_hashes.add(rule_hash)

        return tuple(unique_rules)

    def _compute_rule_hash(self, rule: LearnedRule) -> str:
        keywords_str = " ".join(sorted(rule.pattern.context_keywords))
        patterns_str = " ".join(sorted(rule.pattern.code_patterns[:3]))
        combined = f"{rule.category.value}:{keywords_str}:{patterns_str}"
        return combined.lower().replace(" ", "")

    def _are_similar(self, hash1: str, hash2: str) -> bool:
        if len(hash1) < 10 or len(hash2) < 10:
            return hash1 == hash2
        if hash1 in hash2 or hash2 in hash1:
            return True
        distance = self._levenshtein_distance(hash1, hash2)
        max_len = max(len(hash1), len(hash2))
        similarity = 1 - (distance / max_len)
        return similarity >= self.similarity_threshold

    def _levenshtein_distance(self, s1: str, s2: str) -> int:
        if len(s1) < len(s2):
            return self._levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        previous_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            current_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j + 1]
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        return previous_row[-1]


class RuleValidator:
    def __init__(
        self,
        min_quality_score: float = 0.5,
        min_tool_support: int = 1,
    ) -> None:
        self.min_quality_score = min_quality_score
        self.min_tool_support = min_tool_support

    def validate(self, rule: LearnedRule) -> bool:
        if rule.quality_score < self.min_quality_score:
            return False
        if len(rule.tools) < self.min_tool_support:
            return False
        if not rule.pattern.file_patterns:
            return False
        if not isinstance(rule.category, FeedbackCategory):
            return False
        return True

    def validate_batch(
        self, rules: list[LearnedRule]
    ) -> tuple[LearnedRule, ...]:
        return tuple(rule for rule in rules if self.validate(rule))
