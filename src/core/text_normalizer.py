# -*- coding: utf-8 -*-
"""
Text normalizer for TTS — раскрывает аббревиатуры в фонетическое написание.

Две стратегии:
1. Словарь исключений — аббревиатуры, которые читаются слитно (НАТО, ЮНЕСКО)
   или имеют особое произношение (США → сэ-шэ-а).
2. Автоматическое побуквенное раскрытие для всех остальных аббревиатур
   (2-6 заглавных букв подряд) через таблицу «буква → произношение».
"""

import re
from typing import Optional


# ── Буква → произношение (для побуквенного чтения) ──────────────────

_RU_LETTER_PHONETICS = {
    "А": "а", "Б": "бэ", "В": "вэ", "Г": "гэ", "Д": "дэ",
    "Е": "е", "Ё": "ё", "Ж": "жэ", "З": "зэ", "И": "и",
    "Й": "ий", "К": "ка", "Л": "эл", "М": "эм", "Н": "эн",
    "О": "о", "П": "пэ", "Р": "эр", "С": "эс", "Т": "тэ",
    "У": "у", "Ф": "эф", "Х": "ха", "Ц": "цэ", "Ч": "че",
    "Ш": "ша", "Щ": "ща", "Э": "э", "Ю": "ю", "Я": "я",
}

# Английские буквы → русская транскрипция (для англ. аббревиатур в русском тексте)
_EN_LETTER_PHONETICS_RU = {
    "A": "эй", "B": "би", "C": "си", "D": "ди", "E": "и",
    "F": "эф", "G": "джи", "H": "эйч", "I": "ай", "J": "джей",
    "K": "кей", "L": "эл", "M": "эм", "N": "эн", "O": "оу",
    "P": "пи", "Q": "кью", "R": "ар", "S": "эс", "T": "ти",
    "U": "ю", "V": "ви", "W": "дабл-ю", "X": "экс", "Y": "уай",
    "Z": "зэд",
}

# Английские буквы → английская фонетика (для английского TTS)
_EN_LETTER_PHONETICS_EN = {
    "A": "ay", "B": "bee", "C": "see", "D": "dee", "E": "ee",
    "F": "ef", "G": "jee", "H": "aitch", "I": "eye", "J": "jay",
    "K": "kay", "L": "el", "M": "em", "N": "en", "O": "oh",
    "P": "pee", "Q": "cue", "R": "ar", "S": "es", "T": "tee",
    "U": "you", "V": "vee", "W": "double-you", "X": "ex", "Y": "why",
    "Z": "zed",
}


# ── Словарь исключений ─────────────────────────────────────────────
# Аббревиатуры, которые читаются СЛИТНО (как слово), а не побуквенно.
# Формат: "АББРЕВИАТУРА" → "произношение"
# Если аббревиатуры нет в словаре — она будет раскрыта побуквенно автоматически.

_EXCEPTIONS_RU = {
    # Читаются слитно (как слово)
    "НАТО": "нато",
    "НАСА": "наса",
    "NASA": "наса",
    "NATO": "нато",
    "ЮНЕСКО": "юнеско",
    "UNESCO": "юнеско",
    "МАГАТЭ": "магатэ",
    "ОПЕК": "опек",
    "БРИКС": "брикс",
    "BRICS": "брикс",
    "SWIFT": "свифт",
    "АСЕАН": "асеан",
    "ГЭС": "гэс",
    "АЭС": "аэс",
    "ТЭЦ": "тэц",
    "ВУЗ": "вуз",
    "ЗАГС": "загс",
    "ТАСС": "тасс",
    "МИД": "мид",
    "ОМОН": "омон",
    "СОБР": "собр",
    "ГЛОНАСС": "глонасс",
    "OSINT": "осинт",
    "COVID": "ковид",

    # Особое произношение (не побуквенно и не слитно)
    "США": "сэ-шэ-а",
    "ФРГ": "эф-эр-гэ",
}

_EXCEPTIONS_EN = {
    # Read as words (not letter-by-letter)
    "NATO": "NATO",
    "NASA": "NASA",
    "UNESCO": "UNESCO",
    "ASEAN": "ASEAN",
    "OPEC": "OPEC",
    "BRICS": "BRICS",
    "SWIFT": "SWIFT",
    "COVID": "COVID",
    "SWAT": "SWAT",
    "AWOL": "AWOL",
    "SCUBA": "SCUBA",
    "LASER": "LASER",
    "RADAR": "RADAR",
    "SONAR": "SONAR",
    "CAPTCHA": "CAPTCHA",
}

# ── Паттерн для детекции аббревиатур ────────────────────────────────

# 2-6 заглавных букв подряд (кириллица или латиница), как отдельное слово
_ABBREV_PATTERN = re.compile(
    r'\b([A-ZА-ЯЁ]{2,6})\b'
)

# Слова-исключения, которые не аббревиатуры (предлоги, союзы, местоимения и т.д.)
_NOT_ABBREVIATIONS = frozenset({
    # Русские
    "НА", "НО", "ДА", "НЕ", "НИ", "ОН", "ОТ", "ИЗ", "ПО", "ЗА", "ОБ",
    "ВО", "ДО", "КО", "БЫ", "ЛИ", "ЖЕ", "ТО", "ВСЕ", "ВСЁ", "ЭТО",
    "ТАК", "КАК", "ЧТО", "ГДЕ", "ЕГО", "ИЛИ", "ТУТ", "ВОТ", "ЕЩЕ",
    "ЕЩЁ", "УЖЕ", "ОНА", "ОНИ", "МНЕ",
    # Английские (обычные слова, написанные капсом — не аббревиатуры)
    "OR", "IF", "AN", "AT", "BY", "DO", "GO", "IN", "IS", "IT",
    "ME", "MY", "NO", "OF", "ON", "SO", "TO", "UP", "US", "WE",
    "AM", "AS", "BE", "HE", "OK", "OH",
    "THE", "AND", "FOR", "ARE", "BUT", "NOT", "YOU", "ALL", "CAN",
    "HER", "WAS", "ONE", "OUR", "OUT", "HAS", "HIS", "HOW", "ITS",
    "MAY", "NEW", "NOW", "OLD", "SEE", "WAY", "WHO", "DID", "GET",
    "HIM", "LET", "SAY", "SHE", "TOO", "USE",
    "YES", "HAD", "HAS", "ANY", "FEW", "GOT", "OWN", "END",
    "BIG", "SET", "RUN", "PUT", "ADD", "TRY", "ASK", "MEN",
})


def _is_cyrillic(text: str) -> bool:
    """Проверяет, содержит ли строка кириллические буквы."""
    return bool(re.search(r'[А-ЯЁа-яё]', text))


def _expand_abbreviation(abbrev: str, target_lang: str) -> str:
    """
    Раскрывает аббревиатуру в фонетическое написание.

    Логика:
    1. Проверяет словарь исключений (слитное или особое чтение)
    2. Если не в словаре — побуквенное раскрытие через таблицу фонетики
    """
    lang = target_lang.lower()[:2]

    # Словарь исключений
    if lang == "ru":
        if abbrev in _EXCEPTIONS_RU:
            return _EXCEPTIONS_RU[abbrev]
    else:
        if abbrev in _EXCEPTIONS_EN:
            return _EXCEPTIONS_EN[abbrev]

    # Побуквенное раскрытие
    is_cyr = _is_cyrillic(abbrev)

    if lang == "ru":
        table = _RU_LETTER_PHONETICS if is_cyr else _EN_LETTER_PHONETICS_RU
    else:
        table = _EN_LETTER_PHONETICS_EN

    letters = []
    for ch in abbrev:
        phonetic = table.get(ch.upper())
        if phonetic:
            letters.append(phonetic)
        else:
            letters.append(ch)

    return "-".join(letters)


def normalize_for_tts(text: str, target_lang: str = "ru") -> str:
    """
    Нормализует текст для TTS — раскрывает аббревиатуры в фонетическое написание.

    Args:
        text: Текст для нормализации
        target_lang: Целевой язык (ru, en, ...)

    Returns:
        Текст с раскрытыми аббревиатурами
    """
    if not text:
        return text

    def _replace_match(match: re.Match) -> str:
        word = match.group(1)

        # Пропускаем не-аббревиатуры
        if word in _NOT_ABBREVIATIONS:
            return word

        return _expand_abbreviation(word, target_lang)

    return _ABBREV_PATTERN.sub(_replace_match, text)
