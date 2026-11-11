# Источники и происхождение

Дата исследования: **11 ноября 2026**. Это авторский комплект процедур работы
для Claude Code, ориентированный на выгрузки Конфигуратора. Сторонние исходники,
полные тексты ИТС и HBK в поставку не включены. Нельзя гарантировать глобальную
уникальность всех рекомендаций: язык, стандарт и платформенные ограничения общие.

## Claude Code

| Источник | Что учтено |
|---|---|
| [Skills](https://code.claude.com/docs/en/skills) | SKILL.md, name/description, проектная и личная установка, вызов /name, дополнительные файлы |
| [Memory and rules](https://code.claude.com/docs/en/memory) | Роль CLAUDE.md, тематические правила и paths, разграничение инструкций и реальных ограничений |

Основной файл остаётся коротким; предметные инструкции читаются по задаче.
Не используются автоматические shell-подстановки пользовательских аргументов,
неограниченные allowed-tools, обязательные хуки и отключение разрешений.
Правила — инструкции модели, они не являются технической песочницей.

Для 2.0 повторно открыты Skills и Memory; отдельно прочитаны
[рекомендации Anthropic по созданию навыков](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices).
Применены короткий SKILL.md, конкретное описание срабатывания, тематические
references и исполняемые скрипты. `${CLAUDE_SKILL_DIR}` используется как подстановка
Claude Code, а не как обязательная переменная PowerShell. Проектные rules
ограничены `paths`; это не замена техническим проверкам и разрешениям инструментов.

## Изученные проекты по 1С

| Проект | Пригодные идеи | Что не переносилось |
|---|---|---|
| [Nikolay-Shirokov/cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills), MIT | Инспекция перед изменением; отдельные операции для метаданных, форм, СКД, внешних объектов; CLI Конфигуратора | Код генераторов, полные спецификации, обязательный runtime и ключи без проверки целевой версии |
| [Desko77/claude-code-skills-1c](https://github.com/Desko77/claude-code-skills-1c), MIT | Разделение скиллов и правил; проверка API; доказательства качества; поиск по интерфейсу БСП | EDT-зависимый маршрут, обязательный комментарий для каждого метода, сервисы и каталоги API целиком |
| [Arman-Kudaibergenov/1c-ai-development-kit](https://github.com/Arman-Kudaibergenov/1c-ai-development-kit), README: AGPL-3.0 | Сопоставлены области покрытия и консолидация навыков | Код и тексты не включены; инфраструктурные и EDT-зависимые части не требуются |
| [1c-syntax/bsl-language-server](https://github.com/1c-syntax/bsl-language-server) | Опциональный статический анализ BSL, версии и границы диагностики | Сам анализатор не распространяется с комплектом |
| [Regsorm/bsl-context](https://github.com/Regsorm/bsl-context) | Контекст типов/API из синтакс-помощника, учёт неполного анализа | Сервер и справка не включены |
| [1c-syntax/bsl-context](https://github.com/1c-syntax/bsl-context) | Вариант разбора HBK для локального индекса | Не устанавливается автоматически |

Изучены README, meta-info, skd-edit, db-load-xml и build-spec первого набора;
README, 1c-coding-standards, 1c-platform-docs и 1c-bsp-api второго; README третьего.
Анализ README не означает проверку всех заявленных функций репозитория.

Для воспроизводимости чтения (это **blob SHA файла**, не commit проекта):

| Файл | Blob SHA |
|---|---|
| cc-1c-skills / README.md | c1819af72d54e4131f491d859399ede4e8fab408 |
| cc-1c-skills / meta-info/SKILL.md | c26bb40cceef6a340b4f9f852f84982cb801f774 |
| cc-1c-skills / docs/build-spec.md | 522ca423702d190795332300743b11a90cbffc35 |
| claude-code-skills-1c / README.md | d22f27fce9d4dde4a946e858dae995c7e893a983 |
| claude-code-skills-1c / rules/1c-coding-standards.md | b4e2d69dcfcf34f2034887989c2ced3a6810c429 |
| claude-code-skills-1c / skills/1c-platform-docs/SKILL.md | 82c5db25d7e21ec0cacb8488ec8361361cf3181a |
| claude-code-skills-1c / skills/1c-bsp-api/SKILL.md | 85d542ef59f8426ea83f5b5501d3e1e5ae41e3d9 |

## Официальные материалы 1С

- [Система стандартов ИТС](https://its.1c.ru/db/v8std) — входная точка для актуальных требований.
- [#std453: описание процедур и функций](https://its.1c.ru/db/content/v8std/src/400/100/i8100453.htm) — прочитан доступный текст, в том числе различие публичного и внутреннего метода.
- [#std437: оформление запросов](https://its.1c.ru/db/content/v8std/src/300/100/i8100437.htm) — прочитан доступный текст.
- [#std455: структура модуля](https://its.1c.ru/db/content/v8std/src/400/100/i8100455.htm) — подтверждены название и положения по индексируемому тексту; прямое открытие в этой сессии вернуло 403.
- [#std469: общие модули](https://its.1c.ru/db/content/v8std/src/200/100/i8100469.htm) — подтверждены доступные поисковые выдержки; полный текст не воспроизводится.
- [Отборы в виртуальных таблицах](https://its.1c.ru/db/content/metod8dev/src/developers/platform/metod/query/i8102594.htm) — различие семантики отбора внутри/после СрезПоследних.
- [Руководство 1Ci: проверка конфигурации](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Administrator_Guides/1C_Enterprise_8.3.22_Administrator_Guide/Appendix_7._The_parameters_of_the_command_line_to_launch_1C_Enterprise/7.4._Running_Designer_in_batch_mode/7.4.5._Checking_configurations_and_extensions/) — параметры CheckConfig доступны в поисковом индексе; сверять с руководством установленной версии.

Часть страниц 1Ci перенаправлялась на support.1ci.com; часть ИТС ограничивала
доступ. Полный просмотр закрытой документации не заявляется. Для конкретного
API источником остаётся справка соответствующей сборки и исходники проекта.

## Инфостарт и инструментальные справочники

- [Claude Code + EDT: практический опыт](https://infostart.ru/1c/articles/2815822/) — авторские примеры различий статической и платформенной проверки. EDT-специфичные запреты не перенесены на XML Конфигуратора.
- [BSL Language Server: CLI](https://1c-syntax.github.io/bsl-language-server/) — режим анализа и отчёты.
- [Git attributes](https://git-scm.com/docs/gitattributes), [ls-files](https://git-scm.com/docs/git-ls-files), [add](https://git-scm.com/docs/git-add) — EOL, индекс, renormalize.

## Оригинальные части

Единый маршрут по типу задачи, разделение уровней доказательств, правила
совместимости и работы без EDT, общий инспектор только для чтения, запускатель
этапов сборки/тестов, их тесты, профили и сокращённые правила написаны для комплекта.
Это не объединение чужих репозиториев и не оболочка над обязательным MCP.
При последующем включении стороннего кода отдельно сохранить его лицензию,
уведомления и точную ревизию; настоящее перечисление источников их не заменяет.

## Сборка и автотесты: дополнительное исследование

| Первоисточник | Что проверено |
|---|---|
| [vanessa-runner v3.0.2](https://github.com/vanessa-opensource/vanessa-runner/tree/v3.0.2) | Реальный тег release; README и docs/product/команды: cf, cfe, epf, infobase, test, validate, common-options |
| [Текущая документация vrunner](https://autumn-library.github.io/vanessa-runner/) | Различия с 2.x и с закреплённым release; новые флаги не перенесены молча |
| [Vanessa Automation: запуск](https://github.com/Pr-Mex/vanessa-automation/blob/develop/docs/CommandSetting/CommandSetting.md) | TESTMANAGER, StartFeaturePlayer, VAParams; отдельный запуск от Vanessa-ADD |
| [Vanessa Automation: настройки](https://github.com/Pr-Mex/vanessa-automation/blob/develop/docs/JsonParams/JsonParamsRU.md) | JUnit, клиенты тестирования, Pending, закрытие TestClient |
| [Vanessa Automation: руководство](https://github.com/Pr-Mex/vanessa-automation/blob/develop/docs/index.md) | ЗавершитьРаботуСистемы для закрытия менеджера тестирования |
| [YAxUnit: запуск](https://bia-technologies.github.io/yaxunit/docs/getting-started/run/) и [JSON](https://bia-technologies.github.io/yaxunit/docs/getting-started/run/configuration/) | Конфигуратор/native ENTERPRISE, RunUnitTests, JUnit, exitCode, закрытие клиента |
| [YAxUnit: первый тест](https://github.com/bia-technologies/yaxunit/blob/develop/documentation/docs/getting-started/first-test.md) | Регистрация сценария и базовый интерфейс assertions |
| [OneScript](https://github.com/EvilBeaver/OneScript) | Отдельная среда выполнения; не замена API платформы |
| [OneUnit](https://github.com/sfaqer/OneUnit) | README, packagedef, src/cli/main.os; execute/discover и JUnit |
| [1testrunner](https://github.com/oscript-library/1testrunner) | Индексируемая страница/описание запуска; fetch README.md вернул 404, полный исходный код не исследован |

Зафиксированные blob SHA документации **на теге v3.0.2**:

- cf: a50477de08f585af66bbfcb96bd16d26e1f4b011
- cfe: 31694019808245412290a6920e4758ac6ebf4e90
- epf: 3689f8664477b2cf6cbd857afc47c26876aaeaec
- test: dddeb7b035b38f117e5759c67d5805c28a855ba0
- validate: 3a153d6f2386b27fd27f2e8d05421d9d34b1aea7
- infobase: 4beeb0533ef47d4bcbc48d94aba2d66923f21822
- common-options: 3a29b4ef099d0f9d76a73ad2ec278d6ad8a5a86d

Эти проверки подтверждают содержание документации. Запуска бинарных 1С/CF/CFE,
Vanessa, YAxUnit или OneScript в среде подготовки комплекта не было. Их код и
дистрибутивы не входят в комплект; профили вызывают внешнюю установку пользователя.

## Дополнительные примеры, предложенные пользователем

На 11.11.2026 прочитаны README и выбранные профильные файлы. Это исследование
контрактов, не испытание всех функций проектов. Заимствованы подходы, а не
тексты правил или код. Лицензии ниже — обнаруженные сведения для выбора
интеграции; фактические условия конкретного компонента проверять отдельно.

| Источник | Выбранная идея / ограничение |
|---|---|
| [cc-1c-skills](https://github.com/Nikolay-Shirokov/cc-1c-skills), MIT | XML-операции и генераторы. В 2.0 был внешний адаптер; с 3.0.0 Python-версии включены как движок `engine/` (ревизия и хеши в `engine/manifest.json`), команды — `onec_gen.py` |
| [Desko77](https://github.com/Desko77/claude-code-skills-1c), MIT | Маршрутизация, индекс объектов и поиск интерфейсов БСП; собственная реализация project_context.py |
| [1c-ai-development-kit](https://github.com/Arman-Kudaibergenov/1c-ai-development-kit), AGPL-3.0 | Покрытие областей и консолидация; не навязывать минимальную платформу/EDT из другого набора |
| [ai_rules_1c](https://github.com/comol/ai_rules_1c) | Выбор глубины процесса по риску, доказательства вместо формального PASS; отдельного LICENSE в исследованном корне не обнаружено, материалы не копируются |
| [unica](https://github.com/IngvarConsulting/unica), LGPL-3.0; зависимости отдельно | Типизированные операции, план изменений и честная неполнота. Исследованный README заявляет 8.3.27; не обязательная зависимость старых проектов или 8.5 |
| [1c-ai-feature-dev-workflow](https://github.com/andreeved/1c-ai-feature-dev-workflow), MIT | Исследование → архитектура → реализация → ревью и артефакты задачи. Не переносится ручное создание всех метаданных или согласование каждого этапа |
| [yellow-hammer platform tools](https://marketplace.visualstudio.com/items?itemName=yellow-hammer.1c-platform-tools), [код](https://github.com/yellow-hammer/vscode-1c-platform-tools) | Профили окружения, реальные результаты запуска, несколько тестовых движков. Расширение редактора/MCP — дополнительная интеграция, не обязательная среда Claude CLI |
| [claude-code-bsl-lsp](https://github.com/1c-syntax/claude-code-bsl-lsp), MIT | Навигация и диагностика BSL/OS. SessionStart может обслуживать установку анализатора: версию и обновления проверить перед CI; LSP не компилятор 1С |
| [mcp-1c feenlace](https://github.com/feenlace/mcp-1c), MIT | Индекс выгрузки, справка и подключение к живой базе — разные возможности. Открытый справочник ограничен; полный коммерческий контент не обещается |
| [ssl_3_1](https://github.com/1c-syntax/ssl_3_1), репозиторий указывает CC-BY-4.0 | Поиск определения БСП. Прочитанный description.json: 3.1.12.335; не подмена локального релиза 3.1.7 или другого |
| [1c-mcp Untru](https://github.com/Untru/1c-mcp) | Каталог для обнаружения инструментов, не единый исполняемый MCP; отдельного LICENSE в метаданных не найдено |

Из дополнительных точечных чтений: comol verification-policy; unica
tool-surface и ATTRIBUTIONS; workflow SKILL.md; BSL LSP .lsp.json и SessionStart;
ssl description.json; yellow-hammer ai-mcp/testing. Первоначальные SHA выше
сохраняют происхождение версии 1.0, а manifest генераторов фиксирует новое
чтение конкретной ревизии. Полные чужие инструкции в комплект не включены.

## Повторная сверка 1С/ИТС для 2.0

- [Инкрементальная выгрузка, официальный блог 1С](https://wonderland.v8.1c.ru/blog/inkrementalnaya-vygruzka-konfiguratsii-v-xml/) — назначение файла версий и внешней базы сравнения.
- [Команды конфигураций/расширений 8.3.27](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Administrator_Guides/1C_Enterprise_8.3.27_Administrator_Guide/Appendix_7._Startup_command-line_options_of_1C_Enterprise/7.4._Running_Designer_in_batch_mode/7.4.4._Configurations_and_extensions/?language=tr) — индексируемый официальный текст: полный/частичный режим, списки, параметры. Английский URL перенаправлял на support; полная авторизованная справка не заявляется.
- [Файл различий, 8.3.24](https://kb.1ci.com/1C_Enterprise_Platform/Guides/Administrator_Guides/1C_Enterprise_8.3.24_Administrator_Guide/Appendix_3._Description_and_location_of_internal_files/3.32._File_of_differences_in_configuration_uploads/) — UTF-8, пустой результат, FullDump.
- [ИТС: служебные файлы 8.3.27](https://its.1c.ru/db/v8327doc/bookmark/adm/TI000000365) — подтверждены разделы; оглавление не считается чтением всех внутренних форматов.
- [БСП: переход на новую версию](https://its.1c.ru/db/content/updlib/src/bsp/3.1/updatessl_3.1.html) — требования зависят от конкретного релиза БСП.
- Повторно проверены доступные сведения #std455 и #std469; #std453 по hdoc возвращал оболочку страницы. Ограничение доступа не обходилось, закрытые тексты не распространяются.

Практические решения этих проверок оформлены в platform-matrix.md и
export-contract.md. Ни сторонняя таблица версий, ни результат XML parse
не используются как доказательство совместимости с конкретной сборкой.

Для обратной выгрузки EPF/ERF сверены имена и порядок аргументов с
[описанием параметров запуска от 1С](https://edt.1c.ru/dev/edt/2025.2/apidocs/com/_1c/g5/v8/dt/platform/services/core/runtimes/execution/impl/IRuntimeExecutionCommandOptions.html)
и [официальной публикацией о XML внешних объектов](https://wonderland.v8.1c.ru/blog/razvitie-vygruzki-zagruzki-konfiguratsii-v-iz-xml/).
Первый источник описывает платформенные команды в API интеграции; сам EDT
не используется и не требуется для включённых native-команд Конфигуратора.
