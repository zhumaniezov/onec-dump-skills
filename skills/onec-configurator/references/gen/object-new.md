# onec_gen.py object new — Новый объект метаданных по описанию JSON и регистрация в конфигурации

> Справка к `onec_gen.py object new`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Принимает JSON-определение объекта → генерирует XML + модули в структуре выгрузки конфигурации и
регистрирует объект в `Configuration.xml`.

`ConfigDumpInfo.xml` намеренно не трогается: это служебный файл версий объектов, которым управляет
платформа (для инкрементальной выгрузки).

## Порядок работы

1. Составь JSON по синтаксису ниже → запиши во временный файл.
2. Запусти скрипт.
3. Изменить созданный объект — `object edit`; проверить — `object check`.

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" object new -JsonPath "<json>" -OutputDir "<ConfigDir>"
```

| Параметр | Описание |
|----------|----------|
| `JsonPath` | Путь к JSON-файлу |
| `OutputDir` | Корень выгрузки конфигурации (где `Configuration.xml`, `Catalogs/`, `Documents/`, …) |

## Формат JSON

**Один объект** `{ ... }` или **массив** объектов `[{ ... }, { ... }]` (batch — несколько объектов за прогон).

```json
{ "type": "Catalog", "name": "Номенклатура", "...свойства типа...": "..." }
```

`type` и `name` — обязательные. Остальное — по типу (см. индекс ниже). `synonym` по умолчанию выводится из
`name` (CamelCase → слова через пробел); можно задать явно строкой или мультиязычно: `"synonym": { "ru": "…", "en": "…" }`.

## Реквизиты (shorthand)

Массивы `attributes`, `dimensions`, `resources` и колонки в `tabularSections` задаются строками:

```
"Имя"                          → String(10)
"Имя: Тип"                     → с типом
"Имя: Тип | req, index"        → с флагами
```

**Типы:** `String(100)`, `String(10, fixed)` (фикс. длина), `Number(15,2)`, `Boolean`, `Date`, `DateTime`,
`Time`, ссылочные `CatalogRef.Xxx` / `DocumentRef.Xxx` / `EnumRef.Xxx` / `DefinedType.Xxx` и т.п.
Составной тип — через `+`: `"Значение: String + Number(15,2) + CatalogRef.Контрагенты"`.

**Флаги** (после `|`, через запятую):

| Флаг | Значение | Где |
|------|----------|-----|
| `req` | обязательное заполнение | attributes, dimensions, resources |
| `index` | индексировать | attributes, dimensions |
| `indexAdditional` | индекс с доп. упорядочиванием | attributes |
| `multiline` | многострочное поле | attributes |
| `nonneg` | неотрицательное (Number) | attributes, resources |
| `master` | ведущее измерение | dimensions (регистры) |
| `mainFilter` | основной отбор | dimensions (регистры) |
| `denyIncomplete` | запрет незаполненных | dimensions |
| `useInTotals` | использовать в итогах | dimensions (регистр накопления) |

Реквизиту нужны свойства сверх shorthand (значение заполнения, параметры выбора, формат, подсказка, …) —
задаётся **объектной формой**, см. `references/gen/object-new/reference/attributes.md`.

## Табличные части

```json
"tabularSections": { "Товары": ["Номенклатура: CatalogRef.Номенклатура", "Количество: Number(15,3)"] }
```

Ключ — имя ТЧ, значение — массив колонок (shorthand) ЛИБО объект со свойствами ТЧ (см. `references/gen/object-new/reference/attributes.md`).

## Индекс: свойства по типам

Для каждого типа — свой reference-файл со свойствами, дефолтами и допустимыми значениями:

| Тип(ы) | Файл |
|--------|------|
| Catalog (справочник) | `references/gen/object-new/reference/catalog.md` |
| Document, DocumentJournal, Sequence, DocumentNumerator | `references/gen/object-new/reference/document.md` |
| InformationRegister, AccumulationRegister, AccountingRegister, CalculationRegister | `references/gen/object-new/reference/registers.md` |
| ChartOfAccounts, ChartOfCharacteristicTypes, ChartOfCalculationTypes | `references/gen/object-new/reference/charts.md` |
| ExchangePlan | `references/gen/object-new/reference/exchangeplan.md` |
| BusinessProcess, Task | `references/gen/object-new/reference/process.md` |
| Report, DataProcessor | `references/gen/object-new/reference/report-dataprocessor.md` |
| CommonModule, ScheduledJob, EventSubscription | `references/gen/object-new/reference/code.md` |
| HTTPService, WebService | `references/gen/object-new/reference/web.md` |
| Enum, Constant, DefinedType | `references/gen/object-new/reference/simple.md` |
| ExternalDataSource (внешний источник данных) | `references/gen/object-new/reference/external-data-source.md` |
| FunctionalOption, FilterCriterion, SettingsStorage, CommonForm, CommonPicture, CommonTemplate, служебные | `references/gen/object-new/reference/other-types.md` |

Кросс-типовые детали:
- **`references/gen/object-new/reference/attributes.md`** — объектная форма реквизита и колонки ТЧ (значение заполнения, параметры
  выбора, формат, подсказка, границы, …) + свойства самой ТЧ.
- **`references/gen/object-new/reference/blocks.md`** — блоки объекта: представления, команды (+ характеристики/стандартные реквизиты).

Эта инструкция и reference-файлы — полная документация. Не ищи примеры XML в выгрузках конфигураций.

## Примеры

Справочник с реквизитами:
```json
{ "type": "Catalog", "name": "Организации", "descriptionLength": 100,
  "attributes": ["ИНН: String(12)", "КПП: String(9)", "Директор: CatalogRef.ФизическиеЛица"] }
```

Документ с движениями и ТЧ:
```json
{ "type": "Document", "name": "ПриходнаяНакладная",
  "registerRecords": ["AccumulationRegister.ОстаткиТоваров"],
  "attributes": ["Организация: CatalogRef.Организации", "Контрагент: CatalogRef.Контрагенты"],
  "tabularSections": { "Товары": ["Номенклатура: CatalogRef.Номенклатура", "Количество: Number(15,3)", "Цена: Number(15,2)"] } }
```

Регистр сведений:
```json
{ "type": "InformationRegister", "name": "КурсыВалют", "periodicity": "Day",
  "dimensions": ["Валюта: CatalogRef.Валюты | master, mainFilter, denyIncomplete"],
  "resources": ["Курс: Number(15,4)", "Кратность: Number(10,0)"] }
```

Batch:
```json
[ { "type": "Enum", "name": "Статусы", "values": ["Новый", "Закрыт"] },
  { "type": "Catalog", "name": "Валюты" },
  { "type": "Constant", "name": "ОсновнаяВалюта", "valueType": "CatalogRef.Валюты" } ]
```
