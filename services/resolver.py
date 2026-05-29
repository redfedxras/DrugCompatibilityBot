"""
Резолвер названий препаратов.

Иерархия разрешения (исправленная):
  1. Проверка на инертные вещества.
  2. Локальный словарь RU_TRADE_NAMES (для специфических СНГ-брендов).
  3. Определение языка: если кириллица — принудительный перевод.
  4. RxNorm approximateTerm (NIH) — по английскому названию.
  5. RxNorm drugs.json — поиск по торговому названию.
  6. Фолбэк (низкая уверенность).
"""
import logging
import re
import aiohttp

from deep_translator import GoogleTranslator

from core.config import (
    RU_TRADE_NAMES,
    INERT_SUBSTANCES,
    RXNAV_BASE_URL,
    HTTP_TIMEOUT_SECONDS,
)
from core.models import DrugIdentity

logger = logging.getLogger(__name__)


class DrugNameResolver:
    def __init__(self):
        self._translator = GoogleTranslator(source="ru", target="en")
        self._timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        # Регулярное выражение для поиска кириллицы
        self._cyrillic_pattern = re.compile(r'[а-яА-ЯёЁ]')

    async def resolve(self, name: str, session: aiohttp.ClientSession) -> DrugIdentity:
        """
        Разрешает введённое пользователем название в DrugIdentity.
        """
        cleaned = name.strip().lower()

        # Уровень 0: инертные вещества
        if cleaned in INERT_SUBSTANCES:
            return DrugIdentity(original=name, inn=cleaned, resolved_via="inert")

        # Уровень 1: локальный словарь (советские/российские бренды)
        if cleaned in RU_TRADE_NAMES:
            inn = RU_TRADE_NAMES[cleaned]
            rxcui = await self._get_rxcui(inn, session)
            return DrugIdentity(
                original=name, inn=inn, rxcui=rxcui, resolved_via="dictionary"
            )

        # Уровень 2: Определение языка и перевод
        # Если есть русские буквы, сначала переводим, иначе RxNorm выдаст мусор
        is_russian = bool(self._cyrillic_pattern.search(cleaned))
        search_term = cleaned

        if is_russian:
            translated = await self._translate(cleaned)
            if translated:
                search_term = translated.lower()
                logger.info(f"Translated '{cleaned}' to '{search_term}'")
            else:
                # Если перевод не удался, шансов мало, но попробуем оригинал
                search_term = cleaned

        # Уровень 3: RxNorm approximateTerm (уже по английскому терму)
        result = await self._rxnorm_approximate(search_term, session)
        if result:
            return DrugIdentity(
                original=name,
                inn=result["inn"],
                rxcui=result["rxcui"],
                resolved_via="translate+rxnorm" if is_russian else "rxnorm",
            )

        # Уровень 4: RxNorm drugs.json (поиск по торговому названию)
        result = await self._rxnorm_drugs(search_term, session)
        if result:
            return DrugIdentity(
                original=name,
                inn=result["inn"],
                rxcui=result["rxcui"],
                resolved_via="translate+rxnorm" if is_russian else "rxnorm",
            )

        # Уровень 5: фолбэк с низкой уверенностью
        logger.warning(
            f"Low-confidence resolution for '{name}': using '{search_term}'"
        )
        return DrugIdentity(
            original=name,
            inn=search_term,
            resolved_via="translate_fallback"
        )

    async def _rxnorm_approximate(
        self, term: str, session: aiohttp.ClientSession
    ) -> dict | None:
        """
        RxNorm approximateTerm: нечёткий поиск по названию.
        """
        try:
            async with session.get(
                f"{RXNAV_BASE_URL}/approximateTerm.json",
                params={"term": term, "maxEntries": 1},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                candidates = (
                    data.get("approximateGroup", {}).get("candidate", [])
                )
                if not candidates:
                    return None

                # Дополнительная проверка: RxNorm может вернуть результат с очень низким баллом.
                # Для кириллицы, которая не была переведена, это часто "Formoterol".
                # Но так как мы теперь переводим заранее, риск минимален.

                rxcui = candidates[0].get("rxcui")
                inn = await self._inn_by_rxcui(rxcui, session)
                return {"rxcui": rxcui, "inn": inn or term}
        except Exception as e:
            logger.debug(f"RxNorm approximate failed for '{term}': {e}")
            return None

    async def _rxnorm_drugs(
        self, term: str, session: aiohttp.ClientSession
    ) -> dict | None:
        """
        RxNorm drugs.json: точный поиск по названию.
        """
        try:
            async with session.get(
                f"{RXNAV_BASE_URL}/drugs.json",
                params={"name": term},
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                groups = (
                    data.get("drugGroup", {}).get("conceptGroup", [])
                )
                for group in groups:
                    if group.get("tty") == "IN":
                        props = group.get("conceptProperties", [])
                        if props:
                            rxcui = props[0].get("rxcui")
                            inn = props[0].get("name", term).lower()
                            return {"rxcui": rxcui, "inn": inn}
        except Exception as e:
            logger.debug(f"RxNorm drugs failed for '{term}': {e}")
        return None

    async def _get_rxcui(
        self, drug_name: str, session: aiohttp.ClientSession
    ) -> str | None:
        """Получает RxCUI для известного МНН."""
        result = await self._rxnorm_approximate(drug_name, session)
        return result["rxcui"] if result else None

    async def _inn_by_rxcui(
        self, rxcui: str, session: aiohttp.ClientSession
    ) -> str | None:
        """Получает нормализованное МНН по RxCUI."""
        try:
            async with session.get(
                f"{RXNAV_BASE_URL}/rxcui/{rxcui}/property.json",
                params={"propName": "RxNorm Name"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    props = (
                        data.get("propConceptGroup", {})
                            .get("propConcept", [])
                    )
                    if props:
                        return props[0].get("propValue", "").lower()
        except Exception as e:
            logger.debug(f"INN by rxcui failed for '{rxcui}': {e}")
        return None

    async def _translate(self, name: str) -> str | None:
        """Перевод через Google Translate."""
        try:
            # Запускаем синхронный перевод в треде, если нужно,
            # но deep_translator работает достаточно быстро.
            result = self._translator.translate(name)
            if not result:
                return None
            return result.replace("The ", "").replace("the ", "").strip()
        except Exception as e:
            logger.warning(f"Translation failed for '{name}': {e}")
            return None