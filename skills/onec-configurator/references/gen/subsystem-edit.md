# onec_gen.py subsystem edit — Состав и свойства подсистемы

> Справка к `onec_gen.py subsystem edit`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Точечное редактирование XML подсистемы: состав, дочерние подсистемы, свойства.

## Параметры и команда

| Параметр | Описание |
|----------|----------|
| `SubsystemPath` | Путь к XML-файлу подсистемы |
| `DefinitionFile` | JSON-файл с массивом операций |
| `Operation` | Одна операция (альтернатива DefinitionFile) |
| `Value` | Значение для операции |
| `NoValidate` | Пропустить авто-валидацию |

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" subsystem edit -SubsystemPath '<path>' -Operation add-content -Value 'Catalog.Товары'
```

## Операции

| Операция | Значение | Описание |
|----------|----------|----------|
| `add-content` | `"Catalog.X"` или `["Catalog.X","Document.Y"]` | Добавить объекты в Content |
| `remove-content` | `"Catalog.X"` или `["Catalog.X"]` | Удалить объекты из Content |
| `add-child` | `"ИмяПодсистемы"` | Добавить дочернюю подсистему в ChildObjects |
| `remove-child` | `"ИмяПодсистемы"` | Удалить дочернюю подсистему |
| `set-property` | `{"name":"prop","value":"val"}` | Изменить свойство (Synonym, IncludeInCommandInterface, UseOneCommand, etc.) |

## Примеры

```text
# Добавить объект в состав
... -SubsystemPath Subsystems/Продажи.xml -Operation add-content -Value "Document.Заказ"

# Добавить несколько объектов
... -SubsystemPath Subsystems/Продажи.xml -Operation add-content -Value '["Catalog.Товары","Report.Продажи"]'

# Удалить объект из состава
... -SubsystemPath Subsystems/Продажи.xml -Operation remove-content -Value "Report.Старый"

# Добавить дочернюю подсистему
... -SubsystemPath Subsystems/Продажи.xml -Operation add-child -Value "НоваяДочерняя"

# Изменить свойство
... -SubsystemPath Subsystems/Продажи.xml -Operation set-property -Value '{"name":"IncludeInCommandInterface","value":"false"}'
```
