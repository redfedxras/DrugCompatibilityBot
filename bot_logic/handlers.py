"""
Обработчики Telegram-бота.

Флоу ввода препаратов:
  1. Пользователь вводит название (торговое или МНН, русское или английское)
  2. Бот сразу резолвит его в МНН и показывает результат
  3. Если уверенность высокая — автоматически принимается, просим следующий
  4. Если уверенность низкая — просим подтвердить или ввести МНН вручную
  5. После сбора всех препаратов — запуск анализа
"""
import logging
import aiohttp
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from bot_logic.states import DrugCheck
from bot_logic.formatter import format_full_report, format_resolution_summary
from services.checker import InteractionChecker

logger = logging.getLogger(__name__)
router = Router()

# Инжектируется из main.py после загрузки .env
_checker: InteractionChecker | None = None


def set_checker(checker: InteractionChecker) -> None:
    global _checker
    _checker = checker


# ---------------------------------------------------------------------------
# Клавиатуры
# ---------------------------------------------------------------------------

def _main_menu() -> types.ReplyKeyboardMarkup:
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    builder = ReplyKeyboardBuilder()
    builder.button(text="🔍 Проверить 2 препарата")
    builder.button(text="🔍 Проверить 3 препарата")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True)


def _confirm_keyboard() -> types.ReplyKeyboardMarkup:
    """Кнопки быстрого ответа при подтверждении МНН."""
    from aiogram.utils.keyboard import ReplyKeyboardBuilder
    builder = ReplyKeyboardBuilder()
    builder.button(text="✅ Да, верно")
    builder.button(text="✏️ Ввести МНН вручную")
    builder.adjust(2)
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


# ---------------------------------------------------------------------------
# Команда /start
# ---------------------------------------------------------------------------

@router.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "⚕️ <b>Система анализа лекарственной совместимости</b>\n\n"
        "Выполняю кросс-ресурсную проверку по базам:\n"
        "• NIH RxNav — клинический реестр\n"
        "• FDA Drug Labels — официальные инструкции\n"
        "• FDA FAERS — отчёты о побочных эффектах\n"
        "• PubMed (NCBI) — научные публикации\n\n"
        "💡 <i>Можно вводить торговые названия или МНН, "
        "на русском или английском — бот распознает автоматически.</i>\n\n"
        "Выберите режим:",
        reply_markup=_main_menu(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Выбор режима
# ---------------------------------------------------------------------------

@router.message(F.text.contains("препарата"))
async def choose_mode(message: types.Message, state: FSMContext) -> None:
    count = 2 if "2" in message.text else 3
    await state.update_data(total=count, drugs=[])
    await message.answer(
        f"Режим: <b>{count} препарата</b>.\n\n"
        f"Введите название <b>1-го</b> препарата\n"
        f"<i>(торговое название или МНН, можно на русском)</i>:",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    await state.set_state(DrugCheck.waiting_for_drugs)


# ---------------------------------------------------------------------------
# Сбор названий препаратов
# ---------------------------------------------------------------------------

@router.message(DrugCheck.waiting_for_drugs)
async def collect_drug(message: types.Message, state: FSMContext) -> None:
    """
    Получаем название препарата, сразу резолвим, показываем результат.
    При низкой уверенности — переходим в состояние подтверждения.
    """
    data = await state.get_data()
    drugs: list = data.get("drugs", [])
    raw_input = message.text.strip()

    # Резолвим немедленно
    identity = await _checker.resolve_drug(raw_input)
    drugs.append(identity)
    current = len(drugs)
    total = data["total"]

    # --- Низкая уверенность: translate_fallback означает что RxNorm не нашёл ---
    if identity.resolved_via == "translate_fallback":
        await state.update_data(drugs=drugs)
        await message.answer(
            f"⚠️ Не удалось однозначно распознать «<b>{raw_input}</b>».\n\n"
            f"Найдено похожее МНН: <code>{identity.inn}</code>\n\n"
            f"Это правильный препарат?",
            reply_markup=_confirm_keyboard(),
            parse_mode="HTML",
        )
        # Сохраняем контекст для обработчика подтверждения
        await state.update_data(
            confirm_index=len(drugs) - 1,  # индекс элемента который подтверждаем
            confirm_original=raw_input,
            drugs_count_at_confirm=current,
            total=total,
        )
        await state.set_state(DrugCheck.confirming_inn)
        return

    # --- Высокая уверенность: принимаем автоматически ---
    await state.update_data(drugs=drugs)
    inn_note = (
        f" → <i>{identity.inn}</i>"
        if identity.inn.lower() != raw_input.lower()
        else ""
    )

    if current < total:
        await message.answer(
            f"✅ {raw_input}{inn_note}\n\n"
            f"Введите название <b>{current + 1}-го</b> препарата:",
            parse_mode="HTML",
        )
    else:
        # Все препараты собраны — запускаем анализ
        await message.answer(
            f"✅ {raw_input}{inn_note}",
            parse_mode="HTML",
        )
        await _run_analysis(message, state)


# ---------------------------------------------------------------------------
# Подтверждение МНН при низкой уверенности
# ---------------------------------------------------------------------------

@router.message(DrugCheck.confirming_inn, F.text == "✅ Да, верно")
async def confirm_inn_yes(message: types.Message, state: FSMContext) -> None:
    """Пользователь подтвердил предложенный МНН."""
    data = await state.get_data()
    current = data["drugs_count_at_confirm"]
    total = data["total"]

    if current < total:
        await message.answer(
            f"Введите название <b>{current + 1}-го</b> препарата:",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
        await state.set_state(DrugCheck.waiting_for_drugs)
    else:
        await _run_analysis(message, state)


@router.message(DrugCheck.confirming_inn, F.text == "✏️ Ввести МНН вручную")
async def confirm_inn_manual_prompt(message: types.Message, state: FSMContext) -> None:
    """Просим ввести правильный МНН вручную."""
    data = await state.get_data()
    await message.answer(
        f"Введите МНН препарата на английском языке\n"
        f"<i>(например: nimesulide, ibuprofen, metformin)</i>:",
        reply_markup=types.ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    # Остаёмся в состоянии confirming_inn, но теперь ждём текст


@router.message(DrugCheck.confirming_inn)
async def confirm_inn_receive(message: types.Message, state: FSMContext) -> None:
    """
    Получаем ввод в состоянии confirming_inn.
    Это либо ручной МНН, либо любой другой текст.
    Резолвим ещё раз с тем что ввёл пользователь.
    """
    data = await state.get_data()
    drugs: list = data.get("drugs", [])
    confirm_index: int = data["confirm_index"]
    current = data["drugs_count_at_confirm"]
    total = data["total"]
    raw_input = message.text.strip()

    # Резолвим введённый МНН
    identity = await _checker.resolve_drug(raw_input)

    # Заменяем элемент с низкой уверенностью на подтверждённый
    drugs[confirm_index] = identity
    await state.update_data(drugs=drugs)

    await message.answer(
        f"✅ Принято: <code>{identity.inn}</code>",
        parse_mode="HTML",
    )

    if current < total:
        await message.answer(
            f"Введите название <b>{current + 1}-го</b> препарата:",
            parse_mode="HTML",
        )
        await state.set_state(DrugCheck.waiting_for_drugs)
    else:
        await _run_analysis(message, state)


# ---------------------------------------------------------------------------
# Запуск анализа
# ---------------------------------------------------------------------------

async def _run_analysis(message: types.Message, state: FSMContext) -> None:
    """Запускает анализ всех пар и отправляет отчёт."""
    data = await state.get_data()
    identities = data["drugs"]
    drug_names = [d.original for d in identities]

    # Показываем итоговый список распознанных МНН перед анализом
    resolution_text = format_resolution_summary(identities)
    status = await message.answer(
        f"{resolution_text}\n"
        "🧪 <b>Запускаю анализ...</b>\n"
        "<i>Запрашиваю NIH, FDA и PubMed параллельно. "
        "Обычно занимает 10–20 секунд.</i>",
        parse_mode="HTML",
    )

    try:
        results = await _checker.check_pairs(identities)
        report = format_full_report(drug_names, results)

        if len(report) <= 4096:
            await status.edit_text(
                report, parse_mode="HTML", disable_web_page_preview=True
            )
        else:
            await status.edit_text(
                resolution_text + "\n<i>Отчёт разбит на части:</i>",
                parse_mode="HTML",
            )
            for result in results:
                chunk = format_full_report(drug_names, [result])
                await message.answer(
                    chunk, parse_mode="HTML", disable_web_page_preview=True
                )

    except Exception as e:
        logger.exception(f"Analysis failed: {e}")
        await status.edit_text(
            "⚠️ Ошибка при анализе. Попробуйте позже.\n"
            f"<code>{type(e).__name__}: {e}</code>",
            parse_mode="HTML",
        )

    await message.answer("Новая проверка:", reply_markup=_main_menu())
    await state.clear()