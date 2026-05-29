"""
Синтезатор доказательств.

Объединяет вердикты нескольких источников в один итоговый результат
с использованием взвешенного голосования и расчёта уверенности.

Принцип: источники голосуют своими severity, взвешенными по уровню доказательности.
Итоговый severity — максимальный среди достаточно весомых источников.
"""
import logging

from core.config import (
    SOURCE_WEIGHTS,
    MIN_SOURCE_WEIGHT_THRESHOLD,
    MIN_CONFIDENCE_SOURCES,
    InteractionSeverity,
)
from core.models import DrugIdentity, SourceFinding, PairResult

logger = logging.getLogger(__name__)


class EvidenceSynthesizer:
    def synthesize(
        self,
        drug1: DrugIdentity,
        drug2: DrugIdentity,
        findings: list[SourceFinding],
        articles: list,
    ) -> PairResult:
        """
        Синтезирует итоговый вердикт из результатов всех источников.

        Логика:
        1. Фильтруем недоступные источники (is_available=False)
        2. Взвешиваем каждый результат по SOURCE_WEIGHTS
        3. Итоговый severity = максимальный среди источников с весом > порога
        4. Рассчитываем уверенность = доля ответивших источников
        """
        available = [f for f in findings if f.is_available]
        unavailable_count = len(findings) - len(available)

        if not available:
            logger.warning(f"All sources unavailable for {drug1.inn}+{drug2.inn}")
            return PairResult(
                drug1=drug1,
                drug2=drug2,
                final_severity=InteractionSeverity.UNKNOWN,
                confidence=0.0,
                sources=findings,
                articles=articles,
                low_confidence_warning=True,
            )

        # Собираем взвешенные голоса
        weighted_votes: list[tuple[float, InteractionSeverity]] = []
        for finding in available:
            weight = SOURCE_WEIGHTS.get(finding.source_id, 0.5)
            weighted_votes.append((weight, finding.severity))

        # Итоговый severity — максимальный среди источников с весом выше порога
        # Это консервативный подход: если хотя бы один надёжный источник говорит
        # о серьёзном взаимодействии — мы его не замалчиваем
        significant_severities = [
            severity
            for weight, severity in weighted_votes
            if weight >= MIN_SOURCE_WEIGHT_THRESHOLD
        ]

        if not significant_severities:
            final_severity = InteractionSeverity.UNKNOWN
        else:
            final_severity = max(significant_severities)

        # Уверенность: сколько источников реально ответили
        confidence = len(available) / max(len(findings), 1)
        low_confidence = len(available) < MIN_CONFIDENCE_SOURCES

        if unavailable_count > 0:
            logger.info(
                f"Synthesis for {drug1.inn}+{drug2.inn}: "
                f"{len(available)}/{len(findings)} sources available, "
                f"final={final_severity.name}"
            )

        return PairResult(
            drug1=drug1,
            drug2=drug2,
            final_severity=final_severity,
            confidence=confidence,
            sources=findings,
            articles=articles,
            low_confidence_warning=low_confidence,
        )