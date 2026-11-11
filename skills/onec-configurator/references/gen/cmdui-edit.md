# onec_gen.py cmdui edit — Командный интерфейс подсистемы или конфигурации

> Справка к `onec_gen.py cmdui edit`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Точечное редактирование файла командного интерфейса подсистемы 1С.

## Параметры

| Параметр | Обяз. | Описание |
|----------|:-----:|----------|
| CIPath | да | Путь к CommandInterface.xml |
| Operation | нет | Операция: hide, show, place, order, subsystem-order, group-order |
| Value | нет | Значение для операции |
| DefinitionFile | нет | JSON-файл с массивом операций (альтернатива Operation) |
| CreateIfMissing | нет | Создать файл если не существует |
| NoValidate | нет | Пропустить авто-валидацию |

## Команда

### Inline mode

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" cmdui edit -CIPath '<path>' -Operation hide -Value '<cmd>'
```

### JSON mode

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" cmdui edit -CIPath '<path>' -DefinitionFile '<json>'
```

## Операции

| Операция | Значение | Описание |
|----------|----------|----------|
| hide | Cmd.Name или массив | Скрыть команду (CommandsVisibility, false) |
| show | Cmd.Name или массив | Показать команду (visibility, true) |
| place | {"command":"...","group":"CommandGroup.X"} | Разместить команду в группе |
| order | {"group":"...","commands":[...]} | Задать порядок команд в группе |
| subsystem-order | ["Subsystem.X.Subsystem.A",...] | Порядок дочерних подсистем |
| group-order | ["NavigationPanelOrdinary",...] | Порядок групп |

## Примеры

```text
# Скрыть команду
... -CIPath Subsystems/Продажи/Ext/CommandInterface.xml -Operation hide -Value "Catalog.Товары.StandardCommand.OpenList"

# Показать команду
... -Operation show -Value "Report.Продажи.Command.Отчёт"

# Разместить в группе
... -Operation place -Value '{"command":"Report.X.Command.Y","group":"CommandGroup.Отчеты"}'

# Задать порядок подсистем
... -Operation subsystem-order -Value '["Subsystem.X.Subsystem.A","Subsystem.X.Subsystem.B"]'

# Создать новый CI
... -CIPath <new-path> -Operation subsystem-order -Value '[...]' -CreateIfMissing
```

## Верификация

```
`cmdui check` <CIPath>
```
