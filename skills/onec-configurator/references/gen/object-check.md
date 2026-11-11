# onec_gen.py object check — Проверка XML объекта

> Справка к `onec_gen.py object check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Проверяет XML объекта метаданных из выгрузки конфигурации на структурные ошибки.

## Параметры

| Параметр   | Обяз. | Умолч. | Описание                                      |
|------------|:-----:|---------|-------------------------------------------------|
| ObjectPath | да    | —       | Путь к XML-файлу или каталогу. Через `\|` для batch |
| Detailed   | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors  | нет   | 30      | Остановиться после N ошибок (per object)        |
| OutFile    | нет   | —       | Записать результат в файл (UTF-8 BOM)           |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" object check -ObjectPath "Catalogs/Номенклатура/Номенклатура.xml"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" object check -ObjectPath "Catalogs/Банки|Documents/Заказ"
```
