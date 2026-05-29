"""
Форматирование отчётов для Telegram (HTML-режим).

Вся логика формирования текста сообщений — здесь.
Обработчики не знают ничего о форматировании.
"""
from core.config import (
    InteractionSeverity,
    SEVERITY_EMOJI,
    SEVERITY_LABEL,
    EvidenceLevel,
)
from core.models import PairResult, SourceFinding, DrugIdentity

SOURCE_LABELS = {
    "rxnav_oncology": "NIH RxNav (ONCHigh)",
    "openfda":        "FDA Drug Labels",
    "faers":          "FDA FAERS",
    "pubmed_rct":     "PubMed (РКИ)",
    "pubmed_cohort":  "PubMed (когортные)",
    "pubmed_case":    "PubMed",
}

EVIDENCE_LABELS = {
    EvidenceLevel.A: "A — РКИ/мета-анализы",
    EvidenceLevel.B: "B — когортные исследования",
    EvidenceLevel.C: "C — описания случаев",
    EvidenceLevel.D: "D — экспертное мнение",
}

DISCLAIMER = (
    "\n\n<i>⚠️ ОТКАЗ ОТ ОТВЕТСТВЕННОСТИ: Отчёт сформирован автоматически "
    "на основе баз NIH RxNav, FDA и PubMed. Информация носит справочный "
    "характер и не является медицинской рекомендацией. "
    "Окончательное клиническое решение принимает врач.</i>"
)


def format_resolution_summary(identities: list[DrugIdentity]) -> str:
    """
    Показывает пользователю итоговый список МНН перед запуском анализа.
    Помечает элементы с низкой уверенностью.
    """
    lines = ["<b>Препараты для анализа:</b>\n"]
    for d in identities:
        if d.resolved_via == "translate_fallback":
            lines.append(f"   • {d.original} → <code>{d.inn}</code> ⚠️ <i>(низкая уверенность)</i>\n")
        elif d.inn.lower() != d.original.lower():
            lines.append(f"   • {d.original} → <code>{d.inn}</code>\n")
        else:
            lines.append(f"   • <code>{d.inn}</code>\n")
    lines.append("\n")
    return "".join(lines)


def format_full_report(drug_names: list[str], results: list[PairResult]) -> str:
    """Формирует полный отчёт по всем парам."""
    parts = [
        "<b>📋 ОТЧЁТ О ЛЕКАРСТВЕННОЙ СОВМЕСТИМОСТИ</b>\n",
        f"Состав: {', '.join(drug_names)}\n",
        "─" * 28 + "\n",
    ]
    for result in results:
        parts.append(_format_pair(result))
    parts.append(DISCLAIMER)
    return "".join(parts)


def _format_pair(result: PairResult) -> str:
    """Форматирует блок для одной пары."""
    emoji = SEVERITY_EMOJI[result.final_severity]
    label = SEVERITY_LABEL[result.final_severity]

    lines = [
        f"\n🔹 <b>{_esc(result.pair_label)}</b>\n",
        f"   МНН: <i>{_esc(result.inn_label)}</i>\n\n",
        f"<b>ИТОГОВЫЙ ВЕРДИКТ:</b>\n",
        f"{emoji} <b>{label}</b>\n",
    ]

    # Уверенность
    confidence_pct = int(result.confidence * 100)
    available = sum(1 for s in result.sources if s.is_available)
    total = len(result.sources)
    lines.append(
        f"   Уверенность: {confidence_pct}% "
        f"<i>({available} из {total} источников ответили)</i>\n"
    )

    if result.low_confidence_warning:
        lines.append(
            "   ⚠️ <i>Мало данных — рекомендуется консультация врача</i>\n"
        )

    # Детали по источникам
    if result.sources:
        lines.append("\n<b>По источникам:</b>\n")
        for finding in result.sources:
            lines.append(_format_finding(finding))

    # Публикации PubMed
    if result.articles:
        lines.append("\n<b>📚 Публикации PubMed:</b>\n")
        for article in result.articles:
            clean_title = _esc(article.title[:70])
            pmid = article.pubmed_id
            lines.append(
                f'• <a href="https://pubmed.ncbi.nlm.nih.gov/{pmid}/">'
                f"{clean_title}…</a>\n"
                f"  <i>{_esc(article.relevance_note)}</i>\n"
            )

    lines.append("\n" + "─" * 28 + "\n")
    return "".join(lines)


def _format_finding(finding: SourceFinding) -> str:
    """Форматирует строку одного источника."""
    label = SOURCE_LABELS.get(finding.source_id, finding.source_id)

    if not finding.is_available:
        return f"   ⚪ {label}: <i>недоступен</i>\n"

    emoji = SEVERITY_EMOJI[finding.severity]
    evidence = EVIDENCE_LABELS.get(finding.evidence_level, "")
    line = f"   {emoji} {label}"
    if evidence:
        line += f" <i>[{evidence}]</i>"
    line += "\n"

    if finding.raw_description:
        line += f"      <i>{_esc(finding.raw_description[:220])}</i>\n"

    return line


def _esc(text: str) -> str:
    """Экранирование HTML-спецсимволов."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )