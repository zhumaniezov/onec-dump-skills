> Справка к `onec_gen.py object edit`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.

# Значения перечисления и команды

## add-enumValue / add-command

Просто имена (batch через `;;`):
```text
-Operation add-enumValue -Value "Значение1 ;; Значение2 ;; Значение3"
-Operation add-command -Value "ОткрытьДосье"
```

Команда создаётся вместе с заготовкой модуля `Commands/<Имя>/Ext/CommandModule.bsl`.

## Формы и макеты — другими навыками

Кроме записи в `ChildObjects` у формы и макета есть собственные файлы, поэтому `object edit` их
не добавляет и не удаляет:

| Что | Добавить | Удалить |
|-----|----------|---------|
| Форма | навык `form new` | навык `form remove` |
| Макет | навык `template add` | навык `template remove` |

Содержимое формы собирает навык `form build`.
