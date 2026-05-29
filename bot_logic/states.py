from aiogram.fsm.state import State, StatesGroup


class DrugCheck(StatesGroup):
    waiting_for_drugs = State()  # Ввод названий препаратов по очереди
    confirming_inn = State()     # Подтверждение МНН при низкой уверенности резолвера