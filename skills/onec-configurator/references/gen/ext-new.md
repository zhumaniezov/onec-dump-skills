# onec_gen.py ext new — Каркас нового расширения

> Справка к `onec_gen.py ext new`. Основано на документации открытого проекта (лицензия MIT,
> NOTICE.md). Файл создаётся `scripts/sync_engine.py`, вручную не править.


Создаёт scaffold расширения: `Configuration.xml`, язык в `Languages/`, опционально `Roles/`.

## Подготовка

Если есть выгрузка базовой конфигурации, передай `-ConfigPath` — скрипт автоматически возьмёт из базовой конфигурации `CompatibilityMode` и язык.

### Авто-определение ConfigPath

Если пользователь не указал `-ConfigPath` — попробуй определить автоматически:
1. Прочитай `.v8-project.json` из корня проекта
2. Разреши целевую базу (по имени, ветке или `default` — алгоритм из список баз (`onec_env.py discover`))
3. Если у базы есть поле `configSrc` — используй как `-ConfigPath`
4. Если `configSrc` нет — спроси у пользователя

Если `.v8-project.json` не найден и `-ConfigPath` не задан — расширение создастся с предупреждением (UUID языка = нули, CompatibilityMode по умолчанию).

## Параметры

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `Name` | Имя расширения (обязат.) | — |
| `Synonym` | Синоним | = Name |
| `NamePrefix` | Префикс собственных объектов | = Name + "_" |
| `OutputDir` | Каталог для создания; клади расширение в свой подкаталог, названный по имени: `src\cfe\<Name>` | `src` |
| `Purpose` | `Patch` (исправление) / `Customization` (доработка) / `AddOn` (дополнение) | `Customization` |
| `Version` | Версия расширения | — |
| `Vendor` | Поставщик | — |
| `CompatibilityMode` | Режим совместимости; при заданном `ConfigPath` определяется по базовой конфигурации и этот параметр не нужен | `Version8_3_24` |
| `ConfigPath` | Путь к выгрузке базовой конфигурации (авто-определяет CompatibilityMode и язык) | — |
| `NoRole` | Без основной роли | false |

## Команда

```text
python "${CLAUDE_SKILL_DIR}/scripts/onec_gen.py" ext new -Name "МоёРасширение" -OutputDir "src\cfe\МоёРасширение" -ConfigPath "src\cf"
```

## Примеры

```text
# Расширение для ERP с авто-определением совместимости из базовой конфигурации
... -Name Расш1 -ConfigPath src\cf -OutputDir src\cfe\Расш1

# Расширение-исправление с явным режимом совместимости
... -Name Расш1 -Purpose Patch -CompatibilityMode Version8_3_17 -OutputDir src\cfe\Расш1

# Расширение-доработка с версией
... -Name МоёРасширение -Version "1.0.0.1" -Vendor "Компания" -OutputDir src\cfe\МоёРасширение

# Без роли, с явным префиксом
... -Name ИсправлениеБага -NamePrefix "ИБ_" -Purpose Patch -NoRole -OutputDir src\cfe\ИсправлениеБага
```

## Верификация

```
`ext check` <OutputDir> -ConfigPath <ConfigPath>
```

