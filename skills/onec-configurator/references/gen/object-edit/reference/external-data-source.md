> Справка к `onec_gen.py object edit`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.

# Внешние источники данных

Точечные правки внутри уже созданного источника: поле в таблицу, таблица и функция в источник.
Источник целиком собирает навык `object new` — его повторный запуск заменяет файл источника,
выдаёт новый uuid и оставляет сиротами таблицы, которых нет в описании.

## add-field — поле таблицы

Правится файл таблицы: `ExternalDataSources/<Источник>/Tables/<Таблица>.xml`.

Поле задаётся как обычный реквизит, плюс три своих ключа: `nameInDataSource` (умолчание — имя поля),
`readOnly`, `allowNull`. Флаги строковой формы — `readonly`, `nullable`.

```json
{ "add": { "fields": [
  "barcode: String(20) | nullable",
  { "name": "cost", "type": "Number(15,2)", "nameInDataSource": "cost_net", "readOnly": true }
] } }
```

Типы поля: `Number`, `String`, `Date`, `Boolean`, `UUID`, `BinaryData` и ссылка на таблицу того же
источника — `ExternalDataSourceTableRef.<Источник>.<Таблица>`. Составной тип платформа запрещает.

## add-tables / add-functions — в файле источника

Правится файл источника: `ExternalDataSources/<Источник>.xml`. Таблица — единственная операция
навыка, создающая **файл**: `<Источник>/Tables/<Имя>.xml` плюс имя в `ChildObjects` источника.
Синтаксис таблицы и функции тот же, что у навыка `object new` (`reference/external-data-source.md` там же).

```json
{ "add": {
  "tables": { "sales": { "keyFields": ["id"], "fields": ["id: Number(10,0)", "summa: Number(15,2)"] } },
  "functions": { "nextKey": "NEXT VALUE FOR public.seq_key" }
} }
```

При добавлении таблицы не поддержаны ключи `characteristics` и `default*Form` — навык отвергает их,
а не проглатывает молча. Характеристики задаются при сборке источника навыком `object new`,
форму назначает навык `form new`.

## Удаление

Таблицу удаляет навык `object remove`: `ExternalDataSource.<Источник>.Table.<Таблица>` — вместе с файлом
и записью в `ChildObjects`. Весь источник — `ExternalDataSource.<Источник>`.
