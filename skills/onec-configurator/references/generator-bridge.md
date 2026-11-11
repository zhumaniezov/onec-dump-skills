# Генерация XML через закреплённый внешний инструмент

Базовый маршрут — каркас из собственного Конфигуратора/проекта.
Дополнительный маршрут — `generator_bridge.py` с отдельно установленным
[Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills).
Сторонний код не включён в комплект. Его JSON DSL — API генератора, не схема
XML платформы.

## Поддержанные вызовы

| Действие | Что создаёт | Обязательный вход |
|---|---|---|
| cf-init | Каркас конфигурации | Name, FormatVersion, CompatibilityMode |
| cfe-init | Каркас расширения | Name, base_source с языками основной конфигурации |
| epf-init | Внешняя обработка | Name, FormatVersion |
| erf-init | Внешний отчёт | Name, FormatVersion; WithSKD по необходимости |
| meta-compile | Новый объект | definition, base_source, expected_artifact |
| role-compile | Новая роль | definition, base_source, expected_artifact |
| form-compile | Содержимое управляемой формы | definition, base_source, expected_artifact |
| skd-compile | Схема компоновки | definition |
| mxl-compile | XML табличного макета | definition, base_source, expected_artifact |

Содержимое формы/макета не является полной регистрацией объекта: оболочка
метаданных, владелец, команды и модули проверяются отдельно. Обычные формы и
неподдержанные DSL-конструкции создавать по native-экспорту. Сборка бинарных
CF/CFE/EPF/ERF по-прежнему выполняется платформой.

## Подготовка

В `assets/toolchains/shirokov.json` закреплены commit и Git blob каждого скрипта,
сверенные 11.11.2026. Ревизия:
`3dcb6bf461f6e1f3cf33ee4c60bb51f9f49e1936`.

Один раз подготовить отдельный checkout этой ревизии с отключённой конвертацией
EOL. Не устанавливать чужие hooks/skills в текущий проект автоматически.
Адаптер требует чистый checkout, включая ignored/untracked файлы, и совпадение
байтов скрипта. Обновление revision — явное обслуживание с повторной проверкой
параметров и тестами, не загрузка latest при каждом запуске.

```text
git -c core.autocrlf=false clone https://github.com/Nikolay-Shirokov/cc-1c-skills.git C:/tools/cc-1c-skills-pinned
git -C C:/tools/cc-1c-skills-pinned checkout --detach 3dcb6bf461f6e1f3cf33ee4c60bb51f9f49e1936
```

Установить PowerShell отдельно; выбрать путь к исполняемому файлу. На старых
платформах сначала проверить генератор на выгруженном эталоне. Указанный
FormatVersion не превращает неподдержанную конструкцию в совместимую.

## Запрос и план

`assets/generator-erf.example.json` показывает создание отчёта. Поля запроса:
schema=1, powershell, parameters; для compile — definition; для действий с
основной конфигурацией — base_source. Путь base_source относителен файлу запроса.
В meta/role/form/mxl expected_artifact — новый относительный XML-путь, ещё отсутствующий
в исходной конфигурации. Параметры выходного каталога задаёт только адаптер.

DSL читать в SKILL.md выбранного действия **закреплённого checkout**; не сочинять
неподтверждённые ключи. В запросе не допускается `${...}`, чтобы запускатель
не подставил своё значение внутрь исходного содержимого.

```text
python "${CLAUDE_SKILL_DIR}/scripts/generator_bridge.py" list
python "${CLAUDE_SKILL_DIR}/scripts/generator_bridge.py" plan --checkout "C:/tools/cc-1c-skills-pinned" --action erf-init --request "tools/generator-erf.json" --out "tools/generate-erf.pipeline.json"
python "${CLAUDE_SKILL_DIR}/scripts/pipeline.py" --config "tools/generate-erf.pipeline.json" --project . --run-dir "build/generate-001"
python "${CLAUDE_SKILL_DIR}/scripts/pipeline.py" --config "tools/generate-erf.pipeline.json" --project . --run-dir "build/generate-001" --apply
```

Планирование запускает только локальные команды чтения Git. Генерация требует
`--apply`, выполняется в новом каталоге `run/generator`; для meta/role/form/mxl сначала
создаётся копия base_source. Для CFE база копируется в отдельный входной каталог.
Повторная проверка revision выполняется перед запуском. Логи остаются в
generator.stdout.log/generator.stderr.log.

У form/mxl закреплённый генератор определяет формат по дереву владельца;
в пустом дереве он использует значение по умолчанию. Поэтому адаптер требует
выгрузку конфигурации/расширения и создаёт payload в её копии, например
`expected_artifact: "NewPayload/Form.xml"`. Для payload внутри отдельного
EPF/ERF используйте native-эталон или отдельный проверенный вызов генератора
в дереве именно этого внешнего объекта. Не менять атрибут version вслепую.

## После генерации

1. Проверить audit, состав, UUID/TypeId, ссылки, регистрацию и diff с базой.
2. Перенести только согласованный набор изменений в проект. Учитывать baseline;
   не копировать дерево поверх новых чужих правок.
3. Использовать build-cf/build-cfe/build-epf-erf на выбранной платформе.
4. Проверить контексты, применимость, бизнес-сценарии и обратную выгрузку.

Адаптер — не sandbox для произвольного PowerShell. Checkout и DSL должны быть
доверенными; не менять их параллельно прогону. Защита от ошибочных путей,
закрепление версии и изолированный результат не заменяют проверку стороннего кода.
Unit-тесты адаптера не доказывают, что каждый генератор запускается на Windows
или выдаёт подходящий XML для каждой версии 1С.
