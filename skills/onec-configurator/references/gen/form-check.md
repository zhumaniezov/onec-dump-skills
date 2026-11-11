# onec_gen.py form check — Проверка Form.xml

> Справка к `onec_gen.py form check`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


## Параметры

| Параметр  | Обяз. | Умолч. | Описание                                |
|-----------|:-----:|---------|-----------------------------------------|
| FormPath  | да    | —       | Путь к файлу Form.xml                   |
| Detailed  | нет   | —       | Подробный вывод (все проверки, включая успешные) |
| MaxErrors | нет   | 30      | Остановиться после N ошибок              |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" form check -FormPath "Catalogs/Номенклатура/Forms/ФормаЭлемента"
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" form check -FormPath "src/МояОбработка/Forms/Форма/Ext/Form.xml"
```

