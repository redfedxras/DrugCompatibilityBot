"""
Сервис анализа PubMed.

Роль PubMed в системе: не принятие вердикта, а доказательная база.
Алгоритм анализа абстрактов:
  1. Фильтрация нерелевантных статей (оба препарата должны упоминаться)
  2. Контекстный анализ с учётом отрицаний ("no bleeding" ≠ "bleeding")
  3. Взвешенный подсчёт с учётом типа исследования
"""
import asyncio
import logging
import re
from typing import Optional

from pymed import PubMed

from core.config import (
    NLP_MARKERS,
    PUBMED_MAX_RESULTS_PRIMARY,
    PUBMED_MAX_RESULTS_FALLBACK,
    PUBMED_ARTICLES_IN_REPORT,
    PUBMED_DANGER_THRESHOLD_MAJOR,
    PUBMED_DANGER_THRESHOLD_MODERATE,
    InteractionSeverity,
    EvidenceLevel,
)
from core.models import DrugIdentity, SourceFinding, PubMedArticle

logger = logging.getLogger(__name__)

# Паттерны для определения типа исследования по заголовку/абстракту
_RCT_PATTERNS = re.compile(
    r"randomized|randomised|randomized controlled|clinical trial", re.IGNORECASE
)
_COHORT_PATTERNS = re.compile(
    r"cohort study|observational study|retrospective|prospective study", re.IGNORECASE
)
_CASE_PATTERNS = re.compile(r"case report|case series|case study", re.IGNORECASE)

# Маркеры отрицания — если стоят перед маркером опасности, снижают вес
_NEGATION_PATTERNS = re.compile(
    r"\b(no|not|without|absence of|did not|does not|prevent|avoid|reduce|"
    r"нет|не|отсутствие)\b.{0,30}$",
    re.IGNORECASE,
)


class PubMedService:
    def __init__(self, email: str):
        self._pubmed = PubMed(tool="DrugCompatibilityBot", email=email)

    async def check(
        self, drug1: DrugIdentity, drug2: DrugIdentity
    ) -> tuple[SourceFinding, list[PubMedArticle]]:
        """
        Выполняет поиск в PubMed и анализирует абстракты.
        Возвращает (SourceFinding, список релевантных статей для отчёта).
        Запрос выполняется в executor, так как pymed — синхронная библиотека.
        """
        try:
            articles = await asyncio.to_thread(
                self._fetch_articles, drug1.inn, drug2.inn
            )
        except Exception as e:
            logger.error(f"PubMed fetch error for {drug1.inn}+{drug2.inn}: {e}")
            return (
                SourceFinding(
                    source_id="pubmed_case",
                    severity=InteractionSeverity.UNKNOWN,
                    is_available=False,
                ),
                [],
            )

        if not articles:
            return (
                SourceFinding(
                    source_id="pubmed_case",
                    severity=InteractionSeverity.NONE,
                    raw_description="Клинических публикаций по данной паре в PubMed не найдено",
                    evidence_level=EvidenceLevel.C,
                ),
                [],
            )

        finding, report_articles = self._analyze(articles, drug1.inn, drug2.inn)
        return finding, report_articles

    def _fetch_articles(self, inn1: str, inn2: str) -> list:
        """Синхронный запрос к PubMed (выполняется в executor)."""
        # Приоритетный запрос: MeSH Major Topic + drug interactions
        query_mesh = (
            f'("{inn1}"[MeSH Major Topic] AND "{inn2}"[MeSH Major Topic]) '
            f'AND "drug interactions"[MeSH Terms] AND "humans"[MeSH Terms]'
        )
        results = list(self._pubmed.query(query_mesh, max_results=PUBMED_MAX_RESULTS_PRIMARY))

        # Фолбэк: более мягкий запрос через adverse effects qualifier
        if not results:
            query_soft = (
                f'("{inn1}"[Title/Abstract] AND "{inn2}"[Title/Abstract]) '
                f'AND ("adverse effects"[Subheading] OR "drug interactions"[MeSH Terms]) '
                f'AND "humans"[MeSH Terms]'
            )
            results = list(self._pubmed.query(query_soft, max_results=PUBMED_MAX_RESULTS_FALLBACK))

        return results

    def _analyze(
        self, articles: list, inn1: str, inn2: str
    ) -> tuple[SourceFinding, list[PubMedArticle]]:
        """
        Анализирует список статей.
        Контекстный алгоритм вместо наивного поиска подстрок.
        """
        danger_score = 0.0
        report_articles: list[PubMedArticle] = []
        study_type_source = "pubmed_case"  # по умолчанию — минимальный вес

        for article in articles:
            if not article.title:
                continue

            abstract = article.abstract or ""
            content = f"{article.title} {abstract}".lower()

            # --- Фильтр релевантности ---
            # Статья должна упоминать ОБА препарата (или хотя бы один в заголовке)
            has_drug1 = inn1.lower() in content
            has_drug2 = inn2.lower() in content
            if not (has_drug1 and has_drug2):
                continue

            # --- Определяем тип исследования ---
            if _RCT_PATTERNS.search(content):
                study_type_source = "pubmed_rct"
                type_multiplier = 1.3
            elif _COHORT_PATTERNS.search(content):
                study_type_source = "pubmed_cohort"
                type_multiplier = 1.0
            else:
                type_multiplier = 0.7  # описание случая

            article_score = 0.0
            relevance_notes = []

            # --- Анализ критических маркеров с учётом отрицания ---
            for marker in NLP_MARKERS.critical:
                if marker in content:
                    if self._is_negated(content, marker):
                        article_score -= NLP_MARKERS.critical_weight * 0.3
                    else:
                        article_score += NLP_MARKERS.critical_weight * type_multiplier
                        relevance_notes.append(marker)

            for marker in NLP_MARKERS.serious:
                if marker in content:
                    if not self._is_negated(content, marker):
                        article_score += NLP_MARKERS.serious_weight * type_multiplier
                        relevance_notes.append(marker)

            for marker in NLP_MARKERS.moderate:
                if marker in content:
                    if not self._is_negated(content, marker):
                        article_score += NLP_MARKERS.moderate_weight * type_multiplier

            for marker in NLP_MARKERS.protective:
                if marker in content:
                    article_score += NLP_MARKERS.protective_weight

            danger_score += article_score

            # Добавляем статью в отчёт если она релевантна
            if article_score != 0 and len(report_articles) < PUBMED_ARTICLES_IN_REPORT:
                note = (
                    f"Обнаружены маркеры: {', '.join(relevance_notes[:2])}"
                    if relevance_notes
                    else "Терапевтическая комбинация"
                )
                pubmed_id = str(article.pubmed_id).strip()
                report_articles.append(
                    PubMedArticle(
                        pubmed_id=pubmed_id,
                        title=article.title,
                        relevance_note=note,
                    )
                )

        # --- Вынесение вердикта ---
        if danger_score >= PUBMED_DANGER_THRESHOLD_MAJOR:
            severity = InteractionSeverity.MAJOR
            desc = "Клинически значимые риски подтверждены публикациями PubMed"
        elif danger_score >= PUBMED_DANGER_THRESHOLD_MODERATE:
            severity = InteractionSeverity.MODERATE
            desc = "Умеренные риски взаимодействия отражены в научной литературе"
        elif danger_score < 0:
            severity = InteractionSeverity.NONE
            desc = "Научная литература подтверждает безопасность комбинации"
        else:
            severity = InteractionSeverity.NONE
            desc = "Клинически значимых рисков в PubMed не выявлено"

        evidence = (
            EvidenceLevel.A if study_type_source == "pubmed_rct"
            else EvidenceLevel.B if study_type_source == "pubmed_cohort"
            else EvidenceLevel.C
        )

        finding = SourceFinding(
            source_id=study_type_source,
            severity=severity,
            raw_description=desc,
            evidence_level=evidence,
        )
        return finding, report_articles

    @staticmethod
    def _is_negated(text: str, marker: str) -> bool:
        """
        Проверяет, стоит ли маркер опасности в отрицательном контексте.
        Ищет слова-отрицания в окне 40 символов перед маркером.
        """
        idx = text.find(marker)
        if idx == -1:
            return False
        window = text[max(0, idx - 40): idx]
        return bool(_NEGATION_PATTERNS.search(window))