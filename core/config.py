"""
Централизованная конфигурация проекта.
Все магические числа, константы и настройки — здесь.
"""
from dataclasses import dataclass, field
from enum import IntEnum


# ---------------------------------------------------------------------------
# Уровни доказательности (по иерархии доказательной медицины)
# ---------------------------------------------------------------------------
class EvidenceLevel(IntEnum):
    A = 4  # РКИ, мета-анализы
    B = 3  # Когортные исследования, case-control
    C = 2  # Описания случаев, серии случаев
    D = 1  # Экспертное мнение, консенсус


# ---------------------------------------------------------------------------
# Тяжесть взаимодействия (аналог классификации DrugBank / Lexicomp)
# ---------------------------------------------------------------------------
class InteractionSeverity(IntEnum):
    CONTRAINDICATED = 5   # Абсолютное противопоказание
    MAJOR = 4             # Клинически значимо, требует замены
    MODERATE = 3          # Требует коррекции дозы / мониторинга
    MINOR = 2             # Клинически незначимо
    UNKNOWN = 1           # Данных недостаточно
    NONE = 0              # Взаимодействие не выявлено


SEVERITY_EMOJI = {
    InteractionSeverity.CONTRAINDICATED: "🚫",
    InteractionSeverity.MAJOR:           "🔴",
    InteractionSeverity.MODERATE:        "🟡",
    InteractionSeverity.MINOR:           "🟢",
    InteractionSeverity.UNKNOWN:         "⚪",
    InteractionSeverity.NONE:            "✅",
}

SEVERITY_LABEL = {
    InteractionSeverity.CONTRAINDICATED: "ПРОТИВОПОКАЗАНО",
    InteractionSeverity.MAJOR:           "ОПАСНОЕ ВЗАИМОДЕЙСТВИЕ",
    InteractionSeverity.MODERATE:        "УМЕРЕННОЕ ВЗАИМОДЕЙСТВИЕ",
    InteractionSeverity.MINOR:           "НЕЗНАЧИТЕЛЬНОЕ ВЗАИМОДЕЙСТВИЕ",
    InteractionSeverity.UNKNOWN:         "ДАННЫХ НЕДОСТАТОЧНО",
    InteractionSeverity.NONE:            "ВЗАИМОДЕЙСТВИЕ НЕ ВЫЯВЛЕНО",
}


# ---------------------------------------------------------------------------
# Веса источников для взвешенного синтеза вердикта
# ---------------------------------------------------------------------------
SOURCE_WEIGHTS: dict[str, float] = {
    "rxnav_oncology":  1.0,   # NIH ONCHigh — верифицированный клинический реестр
    "openfda":         0.95,  # Официальные инструкции FDA
    "drugbank":        0.90,  # DrugBank — верифицированная фармакологическая БД
    "pubmed_rct":      0.80,  # PubMed: рандомизированные исследования
    "pubmed_cohort":   0.60,  # PubMed: когортные исследования
    "pubmed_case":     0.35,  # PubMed: описания случаев
    "faers":           0.35,  # FAERS: спонтанные отчёты о побочных эффектах
}

# Минимальный вес источника, чтобы его вердикт влиял на итог
MIN_SOURCE_WEIGHT_THRESHOLD = 0.3

# Порог уверенности: если ответили менее X источников — помечаем как LOW CONFIDENCE
MIN_CONFIDENCE_SOURCES = 2


# ---------------------------------------------------------------------------
# HTTP / API настройки
# ---------------------------------------------------------------------------
HTTP_TIMEOUT_SECONDS = 15
HTTP_MAX_RETRIES = 2

RXNAV_BASE_URL = "https://rxnav.nlm.nih.gov/REST"
OPENFDA_BASE_URL = "https://api.fda.gov/drug"
FAERS_BASE_URL = "https://api.fda.gov/drug/event.json"

PUBMED_MAX_RESULTS_PRIMARY = 15
PUBMED_MAX_RESULTS_FALLBACK = 7
PUBMED_ARTICLES_IN_REPORT = 3


# ---------------------------------------------------------------------------
# NLP — маркеры для анализа абстрактов PubMed
# ---------------------------------------------------------------------------
@dataclass
class NlpMarkers:
    # 1. КРИТИЧЕСКИЕ (Вес 8.0): Летальные исходы, анафилаксия, тяжелейшие синдромы
    critical: list[str] = field(default_factory=lambda: [
        # Общие/Терминальные
        "serotonin syndrome", "torsades de pointes", "anaphylaxis",
        "anaphylactic shock", "fatal", "lethal", "death", "contraindicated",
        "life-threatening", "cardiac arrest", "sudden cardiac death", "mortality increased",
        # Тяжелые поражения кожи и систем
        "stevens-johnson syndrome", "toxic epidermal necrolysis", "angioedema",
        "status epilepticus", "hypertensive crisis", "malignant hyperthermia"
    ])
    critical_weight: float = 8.0

    # 2. СЕРЬЕЗНЫЕ (Вес 4.0): Поражения органов, тяжелые кровотечения, госпитализация
    serious: list[str] = field(default_factory=lambda: [
        # Кровь и сосуды
        "severe bleeding", "major hemorrhage", "intracranial hemorrhage",
        "gastrointestinal perforation", "pancytopenia", "agranulocytosis",
        "myelosuppression", "thrombocytopenia",
        # Органы (Токсичность)
        "hepatotoxicity", "liver failure", "acute liver injury",
        "renal failure", "nephrotoxicity", "acute kidney injury",
        "rhabdomyolysis", "pancreatitis", "ototoxicity",
        # ЦНС и психика
        "seizure", "respiratory depression", "profound sedation", "coma",
        # Сердце
        "prolonged qt", "qtc prolongation", "myocardial infarction", "ventricular arrhythmia"
    ])
    serious_weight: float = 4.0

    # 3. УМЕРЕННЫЕ (Вес 2.0): Изменение уровней, необходимость мониторинга
    moderate: list[str] = field(default_factory=lambda: [
        # Фармакокинетика
        "increased risk", "elevated levels", "reduced efficacy", "plasma concentration increased",
        "decreased clearance", "inhibits the metabolism", "metabolic inhibition",
        # Симптомы и электролиты
        "hypotension", "bradycardia", "tachycardia", "hypertension",
        "hyperkalemia", "hyponatremia", "hypokalemia", "hypoglycemia", "hyperglycemia",
        # Клинические действия
        "monitor", "caution", "dose adjustment required", "dosage reduction",
        "clinically significant interaction"
    ])
    moderate_weight: float = 2.0

    # 4. ЗАЩИТНЫЕ (Вес -3.0): Подтвержденная безопасность или польза
    protective: list[str] = field(default_factory=lambda: [
        "no significant interaction", "safely combined", "well tolerated",
        "no adverse interaction", "therapeutic combination",
        "synergistic benefit", "improved outcomes", "clinically insignificant",
        "no potentiation", "safe profile", "non-significant change",
        "did not alter the pharmacokinetics"
    ])
    protective_weight: float = -3.0


NLP_MARKERS = NlpMarkers()

PUBMED_DANGER_THRESHOLD_MAJOR = 8.0
PUBMED_DANGER_THRESHOLD_MODERATE = 3.0


# ---------------------------------------------------------------------------
# Минимальный словарь только для препаратов, которых нет в RxNorm.
#
# Причина: RxNorm — американская база, она не знает советские/российские
# бренды и препараты, не зарегистрированные в США.
# Всё остальное резолвится через RxNorm API автоматически.
# ---------------------------------------------------------------------------
RU_TRADE_NAMES: dict[str, str] = {
    # Анальгетики и антипиретики
    "анальгин": "metamizole",
    "цитрамон": "aspirin, paracetamol, caffeine",

    # Спазмолитики — нестандартные латинские названия
    "но-шпа":         "drotaverine",
    "дротаверин":     "drotaverine",

    # НПВС — аббревиатуры и советские названия
    "найз":           "nimesulide",
    "нимесил":        "nimesulide",

    # Седативные — советские комбинированные препараты
    "феназепам":      "phenazepam",      # не зарегистрирован в США
    "корвалол":       "phenobarbital",   # комбинированный, основной компонент
    "валокордин":     "phenobarbital",

    # Антигипертензивные
    "конкор":         "bisoprolol",
    "эгилок":         "metoprolol",

    # Антибиотики
    "флемоксин":      "amoxicillin",
    "сумамед":        "azithromycin",
    "клацид":         "clarithromycin",

    # Антикоагулянты
    "ксарелто":       "rivaroxaban",
    "эликвис":        "apixaban",

    # Антидепрессанты
    "ципралекс":      "escitalopram",
    "золофт":         "sertraline",
}

# Инертные вещества — взаимодействие не проверяем
INERT_SUBSTANCES = frozenset([
    "water", "saline", "glucose", "sodium chloride", "distilled water",
    "вода", "физраствор", "глюкоза", "хлорид натрия",
])