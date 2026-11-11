# onec_gen.py config check — Проверка Configuration.xml

> Справка к `onec_gen.py config check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Проверяет Configuration.xml на структурные ошибки: XML well-formedness, InternalInfo, свойства, enum-значения, ChildObjects, DefaultLanguage, файлы языков, каталоги объектов.

## Параметры

| Параметр   | Обяз. | Умолч. | Описание                                      |
|------------|:-----:|---------|-------------------------------------------------|
| ConfigPath | да    | —       | Путь к Configuration.xml или каталогу выгрузки  |
| Detailed   | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors  | нет   | 30      | Остановиться после N ошибок                     |
| OutFile    | нет   | —       | Записать результат в файл (UTF-8 BOM)           |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" config check -ConfigPath "upload/cfempty"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" config check -ConfigPath "upload/cfempty/Configuration.xml"
```
