> Справка к `onec_gen.py object edit`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.

# Табличные части

Операции над табличной частью и её реквизитами. Общие правила shorthand-формата, флагов, batch и
позиционной вставки — в `attributes.md`.

## add-ts

Формат: `ИмяТЧ: Реквизит1: Тип1, Реквизит2: Тип2, ...`

```text
-Operation add-ts -Value "Товары: Ном: CatalogRef.Ном | req, Кол: Число(15,3), Цена: Число(15,2), Сумма: Число(15,2)"
```

## add-ts-attribute / remove-ts-attribute / modify-ts-attribute

Операции над реквизитами **внутри существующей ТЧ**. Формат: `ИмяТЧ.ОпределениеРеквизита` (dot-нотация).

```text
# Добавить реквизит в ТЧ
-Operation add-ts-attribute -Value "Товары.СтавкаНДС: EnumRef.СтавкиНДС"
-Operation add-ts-attribute -Value "Товары.Скидка: Число(15,2) ;; Товары.Бонус: Число(15,2)"

# Позиционная вставка в ТЧ
-Operation add-ts-attribute -Value "Товары.Скидка: Число(15,2) >> after Цена"

# Удалить реквизит из ТЧ
-Operation remove-ts-attribute -Value "Товары.УстаревшийРекв"
-Operation remove-ts-attribute -Value "Товары.Рекв1 ;; Товары.Рекв2"

# Изменить реквизит в ТЧ (rename, type change и т.д.)
-Operation modify-ts-attribute -Value "Товары.СтароеИмя: name=НовоеИмя, type=Строка(500)"
```

Batch через `;;` — можно указать разные ТЧ: `"Товары.А: Строка(50) ;; Услуги.Б: Число(10)"`.

Составной тип реквизита ТЧ:
```text
-Operation modify-ts-attribute -Value "Данные.Значение: type=Строка + Число(15,2) + Дата"
```

## modify-ts

Изменение свойств **самой табличной части** (Synonym, FillChecking, Use и др.):

```text
-Operation modify-ts -Value "Товары: synonym=Товарный состав"
-Operation modify-ts -Value "Товары: fillChecking=ShowError"
```

Формат аналогичен `modify-attribute`: `ИмяТЧ: ключ=значение, ключ=значение`.

## remove-ts

```text
-Operation remove-ts -Value "УстаревшаяТЧ"
```
