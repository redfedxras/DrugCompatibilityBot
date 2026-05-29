"""
Модели данных проекта.
Используем dataclasses + строгую типизацию вместо голых словарей.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from core.config import InteractionSeverity, EvidenceLevel


@dataclass(frozen=True)
class DrugIdentity:
    """Нормализованная идентификация препарата."""
    original: str           # Введённое пользователем название
    inn: str                # МНН (английское)
    rxcui: Optional[str] = None        # Идентификатор RxNorm
    resolved_via: str = "dictionary"   # Как было разрешено имя


@dataclass
class SourceFinding:
    """Результат проверки из одного источника."""
    source_id: str                          # Идентификатор источника (из SOURCE_WEIGHTS)
    severity: InteractionSeverity
    mechanism: Optional[str] = None         # Механизм взаимодействия
    effect: Optional[str] = None            # Клинический эффект
    management: Optional[str] = None        # Рекомендация по ведению
    evidence_level: EvidenceLevel = EvidenceLevel.D
    raw_description: Optional[str] = None   # Оригинальный текст из источника
    is_available: bool = True               # False если источник недоступен


@dataclass
class PubMedArticle:
    """Статья PubMed, релевантная для отчёта."""
    pubmed_id: str
    title: str
    relevance_note: str   # Почему она попала в отчёт


@dataclass
class PairResult:
    """Итоговый результат анализа пары препаратов."""
    drug1: DrugIdentity
    drug2: DrugIdentity
    final_severity: InteractionSeverity
    confidence: float                        # 0.0 – 1.0
    sources: list[SourceFinding] = field(default_factory=list)
    articles: list[PubMedArticle] = field(default_factory=list)
    low_confidence_warning: bool = False

    @property
    def pair_label(self) -> str:
        return f"{self.drug1.original.upper()} + {self.drug2.original.upper()}"

    @property
    def inn_label(self) -> str:
        return f"{self.drug1.inn} + {self.drug2.inn}"