"""
Главный оркестратор проверки взаимодействий.

check_pairs теперь принимает уже готовые DrugIdentity — резолвинг
вынесен в handlers, чтобы пользователь мог подтвердить МНН до запуска анализа.
"""
import asyncio
import logging
import aiohttp
from itertools import combinations

from core.config import HTTP_TIMEOUT_SECONDS, InteractionSeverity
from core.models import DrugIdentity, SourceFinding, PairResult
from services.resolver import DrugNameResolver
from services.rxnav_service import RxNavService
from services.openfda_service import OpenFDAService
from services.pubmed_service import PubMedService
from services.synthesizer import EvidenceSynthesizer

logger = logging.getLogger(__name__)


class InteractionChecker:
    """
    Фасад над всеми сервисами.

    Публичный интерфейс:
      - resolve_drug()  — резолвинг одного названия (вызывается из handlers при каждом вводе)
      - check_pairs()   — анализ уже готовых DrugIdentity
    """

    def __init__(self, pubmed_email: str):
        self.resolver = DrugNameResolver()   # публичный — handlers обращаются напрямую
        self._rxnav = RxNavService()
        self._fda = OpenFDAService()
        self._pubmed = PubMedService(email=pubmed_email)
        self._synthesizer = EvidenceSynthesizer()
        self._timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async def resolve_drug(self, name: str) -> DrugIdentity:
        """
        Резолвит одно название препарата.
        Создаёт отдельную сессию — используется из handlers при каждом вводе.
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            return await self.resolver.resolve(name, session)

    async def check_pairs(self, identities: list[DrugIdentity]) -> list[PairResult]:
        """
        Принимает список уже нормализованных DrugIdentity,
        возвращает результаты для всех пар.
        Использует одну HTTP-сессию для всех запросов.
        """
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            pairs = list(combinations(identities, 2))
            results = await asyncio.gather(
                *[self._check_pair(d1, d2, session) for d1, d2 in pairs],
                return_exceptions=True,
            )

        pair_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Pair check failed for pair {i}: {result}")
                d1, d2 = pairs[i]
                pair_results.append(PairResult(
                    drug1=d1, drug2=d2,
                    final_severity=InteractionSeverity.UNKNOWN,
                    confidence=0.0,
                    low_confidence_warning=True,
                ))
            else:
                pair_results.append(result)

        return pair_results

    async def _check_pair(
        self,
        drug1: DrugIdentity,
        drug2: DrugIdentity,
        session: aiohttp.ClientSession,
    ) -> PairResult:
        """Параллельный запрос ко всем источникам для одной пары."""

        # Инертные вещества — без запросов
        if drug1.resolved_via == "inert" or drug2.resolved_via == "inert":
            return PairResult(
                drug1=drug1, drug2=drug2,
                final_severity=InteractionSeverity.NONE,
                confidence=1.0,
                sources=[SourceFinding(
                    source_id="rxnav_oncology",
                    severity=InteractionSeverity.NONE,
                    raw_description="Инертное вещество — взаимодействие клинически невозможно",
                )],
            )

        rxnav_result, fda_label_result, fda_faers_result, pubmed_result = (
            await asyncio.gather(
                self._rxnav.check(drug1, drug2, session),
                self._fda.check_label(drug1, drug2, session),
                self._fda.check_faers(drug1, drug2, session),
                self._pubmed.check(drug1, drug2),
                return_exceptions=True,
            )
        )

        findings: list[SourceFinding] = []
        articles = []

        for result in (rxnav_result, fda_label_result, fda_faers_result):
            if isinstance(result, Exception):
                logger.error(f"Source error: {result}")
            elif isinstance(result, SourceFinding):
                findings.append(result)

        if isinstance(pubmed_result, tuple):
            pubmed_finding, articles = pubmed_result
            findings.append(pubmed_finding)
        elif isinstance(pubmed_result, Exception):
            logger.error(f"PubMed error: {pubmed_result}")

        return self._synthesizer.synthesize(drug1, drug2, findings, articles)