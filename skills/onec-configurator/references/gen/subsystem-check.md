# onec_gen.py subsystem check — Проверка подсистемы

> Справка к `onec_gen.py subsystem check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Проверяет структурную корректность XML-файла подсистемы из выгрузки конфигурации.

## Параметры

| Параметр      | Обяз. | Умолч. | Описание                                  |
|---------------|:-----:|---------|--------------------------------------------|
| SubsystemPath | да    | —       | Путь к XML-файлу подсистемы                |
| Detailed      | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors     | нет   | 30      | Остановиться после N ошибок                |
| OutFile       | нет   | —       | Записать результат в файл                  |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" subsystem check -SubsystemPath "Subsystems/Продажи"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" subsystem check -SubsystemPath "Subsystems/Продажи.xml"
```
