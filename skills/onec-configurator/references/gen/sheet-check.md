# onec_gen.py sheet check — Проверка табличного макета

> Справка к `onec_gen.py sheet check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Проверяет Template.xml на структурные ошибки: индексы, ссылки на палитры, диапазоны именованных областей и объединений, согласованность ячеек-полей ввода.

## Параметры

| Параметр      | Обяз. | Умолч. | Описание                                 |
|---------------|:-----:|---------|--------------------------------------------|
| TemplatePath  | да    | —       | Путь к макету (директория или Template.xml) |
| Detailed      | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors     | нет   | 20      | Остановиться после N ошибок                |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" sheet check -TemplatePath "Catalogs/Номенклатура/Templates/Макет"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" sheet check -TemplatePath "src/МояОбработка/Templates/ПечатнаяФорма"
```

