# NOTICE

Проект использует идеи и данные сторонних проектов. Ниже перечислено, что и на каких условиях.

## Состав

- Скилл `onec-configurator` (справочники, `source_edit.py`, `dump_tool.py`, `project_context.py`,
  `pipeline.py`, `generator_bridge.py`, профили, шаблоны) — автор владелец репозитория, версия 2.0.0.
  Источники, изученные при его создании, — `docs/provenance/sources-onec-configurator.md`.
- `onec_index.py`, `v8container.py`, `navigation.md` — из проекта onec-dump 0.1.

## Планируется использовать с сохранением авторства

| Проект | Лицензия | Что берём |
|---|---|---|
| [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills) | MIT, © Nick Shirokov | Описания XML-формата выгрузки (`docs/1c-*-spec.md`) как справочный материал и идеи сводных скриптов `*-info`. Конкретные файлы будут указаны здесь по мере переноса. |
| [Desko77/claude-code-skills-1c](https://github.com/Desko77/claude-code-skills-1c) | MIT | Идеи индекса выгрузки и проверки запросов по индексу. |
| [zeegin/v8std](https://github.com/zeegin/v8std) | Репозиторий CC0; права на тексты стандартов принадлежат фирме «1С» | Источник офлайн-снимка стандартов и соответствий кодов диагностик пунктам стандартов. Тексты стандартов включены в каталог `skills/onec-configurator/standards/` с указанием источника и правообладателя; по требованию правообладателя они будут удалены. |

## Лицензия MIT и тексты стандартов

Лицензия MIT проекта распространяется на код и собственные материалы. На тексты стандартов
в `skills/onec-configurator/standards/` она **не распространяется**: права на них принадлежат фирме «1С».

## Изучено, код и тексты не копируются

- [Unica](https://github.com/IngvarConsulting/unica) (LGPL-3.0), [1c-ai-development-kit](https://github.com/Arman-Kudaibergenov/1c-ai-development-kit) (AGPL-3.0), [ai_rules_1c](https://github.com/comol/ai_rules_1c) (лицензия не указана): использовались только для анализа подходов.

## Тестовые данные

- Выгрузка БСП [1c-syntax/ssl_3_1](https://github.com/1c-syntax/ssl_3_1) (формат 2.20) используется как публичный крупный образец. В репозиторий не включается, загружается скриптом.
