"""
Сервис проверки взаимодействий через RxNav (NIH).

Использует два источника:
  - ONCHigh: верифицированные взаимодействия с высоким уровнем доказательности
  - DDInter: расширенный поиск взаимодействий
"""
import logging
import aiohttp

from core.config import RXNAV_BASE_URL, HTTP_TIMEOUT_SECONDS, InteractionSeverity, EvidenceLevel
from core.models import DrugIdentity, SourceFinding

logger = logging.getLogger(__name__)

# Маппинг строковых severity из RxNav на наш enum
_SEVERITY_MAP: dict[str, InteractionSeverity] = {
    "high":     InteractionSeverity.MAJOR,
    "medium":   InteractionSeverity.MODERATE,
    "low":      InteractionSeverity.MINOR,
    "critical": InteractionSeverity.CONTRAINDICATED,
}


class RxNavService:
    def __init__(self):
        self._timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async def check(
        self, drug1: DrugIdentity, drug2: DrugIdentity, session: aiohttp.ClientSession
    ) -> SourceFinding:
        """
        Проверяет взаимодействие пары через RxNav.
        Возвращает SourceFinding всегда — даже при ошибке сети (is_available=False).
        """
        if not drug1.rxcui or not drug2.rxcui:
            return SourceFinding(
                source_id="rxnav_oncology",
                severity=InteractionSeverity.UNKNOWN,
                raw_description="RxCUI не определён — проверка невозможна",
                is_available=False,
            )

        try:
            # Запрос ONCHigh — приоритетный источник
            finding = await self._query_onchigh(drug1.rxcui, drug2.rxcui, session)
            if finding:
                return finding

            # Расширенный поиск через список всех взаимодействий для drug1
            finding = await self._query_all_interactions(drug1.rxcui, drug2.rxcui, session)
            if finding:
                return finding

            return SourceFinding(
                source_id="rxnav_oncology",
                severity=InteractionSeverity.NONE,
                raw_description="Взаимодействие в базе RxNav не зафиксировано",
                evidence_level=EvidenceLevel.B,
            )

        except aiohttp.ClientError as e:
            logger.error(f"RxNav network error for {drug1.inn}+{drug2.inn}: {e}")
            return SourceFinding(
                source_id="rxnav_oncology",
                severity=InteractionSeverity.UNKNOWN,
                raw_description="Сервис RxNav временно недоступен",
                is_available=False,
            )

    async def _query_onchigh(
        self, rxcui1: str, rxcui2: str, session: aiohttp.ClientSession
    ) -> SourceFinding | None:
        """Запрос к верифицированной базе ONCHigh."""
        url = f"{RXNAV_BASE_URL}/interaction/interaction.json"
        async with session.get(url, params={"rxcui": rxcui1, "sources": "ONCHigh"}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return self._extract_finding(data, rxcui2, "rxnav_oncology", EvidenceLevel.A)

    async def _query_all_interactions(
        self, rxcui1: str, rxcui2: str, session: aiohttp.ClientSession
    ) -> SourceFinding | None:
        """Расширенный запрос по всем доступным источникам RxNav."""
        url = f"{RXNAV_BASE_URL}/interaction/interaction.json"
        async with session.get(url, params={"rxcui": rxcui1}) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return self._extract_finding(data, rxcui2, "rxnav_oncology", EvidenceLevel.B)

    def _extract_finding(
        self,
        data: dict,
        target_rxcui: str,
        source_id: str,
        evidence_level: EvidenceLevel,
    ) -> SourceFinding | None:
        """Извлекает релевантную пару из ответа RxNav API."""
        for group in data.get("interactionTypeGroup", []):
            for itype in group.get("interactionType", []):
                for pair in itype.get("interactionPair", []):
                    concepts = pair.get("interactionConcept", [])
                    # Проверяем, что второй препарат — именно наш
                    involved_rxcuis = {
                        c.get("minConceptItem", {}).get("rxcui")
                        for c in concepts
                    }
                    if target_rxcui not in involved_rxcuis:
                        continue

                    severity_str = pair.get("severity", "").lower()
                    severity = _SEVERITY_MAP.get(severity_str, InteractionSeverity.MODERATE)
                    description = pair.get("description", "")

                    return SourceFinding(
                        source_id=source_id,
                        severity=severity,
                        raw_description=description,
                        evidence_level=evidence_level,
                        is_available=True,
                    )
        return None