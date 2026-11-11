# onec_gen.py form remove — Удалить форму объекта

> Справка к `onec_gen.py form remove`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Удаляет форму и убирает её регистрацию из корневого XML объекта.

## Usage

```
`form remove` <ObjectName> <FormName>
```

| Параметр   | Обязательный | По умолчанию | Описание                            |
|------------|:------------:|--------------|-------------------------------------|
| ObjectName | да           | —            | Имя объекта                         |
| FormName   | да           | —            | Имя формы для удаления              |
| SrcDir     | нет          | `src`        | Каталог исходников                  |
| Force      | нет          | —            | Удалить, даже если на форму ссылаются, и очистить ссылки |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" form remove -ObjectName "<ObjectName>" -FormName "<FormName>" [-SrcDir "<SrcDir>"] [-Force]
```

## Что удаляется

```
<SrcDir>/<ObjectName>/Forms/<FormName>.xml     # Метаданные формы
<SrcDir>/<ObjectName>/Forms/<FormName>/         # Каталог формы (рекурсивно)
```

## Что модифицируется

- `<SrcDir>/<ObjectName>.xml` — убирается `<Form>` из `ChildObjects`
- Свойства объекта, указывавшие на удалённую форму — очищаются
