# onec_gen.py cmdui check — Проверка командного интерфейса

> Справка к `onec_gen.py cmdui check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Проверяет XML командного интерфейса на структурные ошибки: корневой элемент, допустимые секции, порядок, формат ссылок на команды, дубликаты.

## Параметры

| Параметр  | Обяз. | Умолч. | Описание                                |
|-----------|:-----:|---------|-----------------------------------------|
| CIPath    | да    | —       | Путь к CommandInterface.xml             |
| Detailed  | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors | нет   | 30      | Остановиться после N ошибок              |
| OutFile   | нет   | —       | Записать результат в файл (UTF-8 BOM)   |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" cmdui check -CIPath "Subsystems/Продажи"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" cmdui check -CIPath "Subsystems/Продажи/Ext/CommandInterface.xml"
```
