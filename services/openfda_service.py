"""
Сервис OpenFDA.

Два источника:
  1. Drug Labels API — официальные инструкции FDA (секция drug interactions)
  2. FAERS API — база спонтанных отчётов о побочных эффектах
"""
import logging
import aiohttp
from urllib.parse import quote

from core.config import (
    OPENFDA_BASE_URL, FAERS_BASE_URL,
    HTTP_TIMEOUT_SECONDS, InteractionSeverity, EvidenceLevel,
)
from core.models import DrugIdentity, SourceFinding

logger = logging.getLogger(__name__)


class OpenFDAService:
    def __init__(self):
        self._timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async def check_label(
        self, drug1: DrugIdentity, drug2: DrugIdentity, session: aiohttp.ClientSession
    ) -> SourceFinding:
        """
        Ищет упоминание drug2 в официальной инструкции drug1 (раздел drug_interactions).
        Официальный label — высокий уровень доказательности.
        """
        try:
            # Запрос секции drug_interactions из FDA label
            query = (
                f'openfda.generic_name:"{drug1.inn}" '
                f'AND drug_interactions:"{drug2.inn}"'
            )
            url = f"{OPENFDA_BASE_URL}/label.json"
            async with session.get(url, params={"search": query, "limit": 1}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("results", [])
                    if results:
                        interactions_text = " ".join(
                            results[0].get("drug_interactions", [""])
                        )
                        severity = self._assess_label_severity(interactions_text, drug2.inn)
                        return SourceFinding(
                            source_id="openfda",
                            severity=severity,
                            raw_description=self._truncate(interactions_text, 300),
                            evidence_level=EvidenceLevel.A,
                            is_available=True,
                        )

            return SourceFinding(
                source_id="openfda",
                severity=InteractionSeverity.NONE,
                raw_description="В инструкции FDA взаимодействие не упомянуто",
                evidence_level=EvidenceLevel.B,
            )

        except aiohttp.ClientError as e:
            logger.error(f"OpenFDA label error: {e}")
            return SourceFinding(
                source_id="openfda",
                severity=InteractionSeverity.UNKNOWN,
                is_available=False,
            )

    async def check_faers(
        self, drug1: DrugIdentity, drug2: DrugIdentity, session: aiohttp.ClientSession
    ) -> SourceFinding:
        """
        FAERS: ищет совместные отчёты о побочных эффектах для пары препаратов.
        Низкий уровень доказательности (спонтанные отчёты), но широкий охват.
        """
        try:
            query = (
                f'patient.drug.medicinalproduct:"{drug1.inn}" '
                f'AND patient.drug.medicinalproduct:"{drug2.inn}"'
            )
            async with session.get(FAERS_BASE_URL, params={"search": query, "limit": 5}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total = data.get("meta", {}).get("results", {}).get("total", 0)

                    if total == 0:
                        return SourceFinding(
                            source_id="faers",
                            severity=InteractionSeverity.NONE,
                            raw_description="В FAERS совместных случаев не зафиксировано",
                            evidence_level=EvidenceLevel.C,
                        )

                    # Анализируем серьёзность отчётов
                    serious_count = sum(
                        1 for r in data.get("results", [])
                        if r.get("serious") == "1"
                    )
                    severity = (
                        InteractionSeverity.MAJOR
                        if serious_count >= 3
                        else InteractionSeverity.MODERATE
                        if serious_count >= 1
                        else InteractionSeverity.MINOR
                    )
                    return SourceFinding(
                        source_id="faers",
                        severity=severity,
                        raw_description=(
                            f"В FAERS найдено {total} совместных отчётов "
                            f"({serious_count} серьёзных)"
                        ),
                        evidence_level=EvidenceLevel.C,
                    )

            return SourceFinding(
                source_id="faers",
                severity=InteractionSeverity.UNKNOWN,
                is_available=False,
            )

        except aiohttp.ClientError as e:
            logger.error(f"FAERS error: {e}")
            return SourceFinding(
                source_id="faers",
                severity=InteractionSeverity.UNKNOWN,
                is_available=False,
            )

    def _assess_label_severity(self, text: str, drug2_name: str) -> InteractionSeverity:
        """Оценивает тяжесть по тексту инструкции FDA."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["contraindicated", "do not use", "must not"]):
            return InteractionSeverity.CONTRAINDICATED
        if any(w in text_lower for w in ["serious", "severe", "fatal", "life-threatening"]):
            return InteractionSeverity.MAJOR
        if any(w in text_lower for w in ["caution", "monitor", "adjust dose", "increased risk"]):
            return InteractionSeverity.MODERATE
        if drug2_name.lower() in text_lower:
            return InteractionSeverity.MINOR
        return InteractionSeverity.NONE

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        return text[:max_len] + "…" if len(text) > max_len else text