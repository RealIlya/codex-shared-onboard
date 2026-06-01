# Codex Shared Onboarding

[English version](README.md)

`codex_shared_onboard.py` помогает подключить машину к безопасному shared-слою Codex:

- `.codex-shared` синхронизируется между машинами через Syncthing.
- `.codex` целиком не синхронизируется.
- Пользовательские skills подключаются из `.codex-shared/skills-user` в локальный `.codex/skills`.
- Codex memories можно публиковать в `.codex-shared/memories-published/current` и забирать на reader-машины как локальные копии.
- Runtime-состояние Codex остаётся локальным: `config.toml`, `auth.json`, `rules/`, `sessions/`, `history.jsonl`, `state_*.sqlite*`, `logs_*.sqlite*`, `cache/`, `tmp/`, `.tmp/`, `.system/`.

По умолчанию скрипт работает в dry-run режиме. Реальные изменения выполняются только с `--apply`.

## Требования

- Python 3.10+.
- Установленный Codex CLI.
- Syncthing, если нужна синхронизация между машинами.
- На Windows скрипт использует junction для директорий, если обычный symlink недоступен.

## Быстрая Проверка

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
```

`self-test` работает только во временных папках внутри текущей директории. Он не трогает реальный `.codex`.

## Справочник Команд

Общий формат:

```bash
python codex_shared_onboard.py [global-options] <command> [command-options]
```

После установки launcher можно использовать `codex-shared-onboard` вместо `python codex_shared_onboard.py`.

Глобальные флаги:

```text
--codex-dir PATH            Локальный Codex home. По умолчанию ~/.codex.
--shared-dir PATH           Shared-директория. По умолчанию ~/.codex-shared, или /mnt/c/Users/<WindowsUser>/.codex-shared в WSL, если путь удалось определить.
--apply                     Реально менять файлы. Без него mutating-команды работают в dry-run режиме.
--verbose                   Печатать дополнительные диагностические детали для subprocess/API-вызовов.
--syncthing-url URL         URL Syncthing REST API. По умолчанию http://127.0.0.1:8384.
--syncthing-api-key KEY     API key Syncthing. Если не указан, скрипт пытается прочитать его из локального Syncthing config.
--version                   Показать версию инструмента и выйти.
-h, --help                  Показать help.
```

Команды:

```text
install                     Подготовить .codex-shared и подключить shared user skills в .codex/skills.
doctor                      Проверить paths, tools, shared-файлы, local/published memory status, conflicts и skill links.
snapshot                    Сделать локальный Git snapshot директории .codex-shared.
memories adopt              DEPRECATED: скопировать локальную .codex/memories в .codex-shared/memories и залинковать локальные memories на shared-директорию.
memories link               DEPRECATED: подключить локальную .codex/memories к существующей .codex-shared/memories без копирования local reader memories поверх shared memories.
memories publish            Скопировать локальную .codex/memories в .codex-shared/memories-published/current и сохранить snapshot.
memories consume            Заменить локальную .codex/memories валидированной копией .codex-shared/memories-published/current.
install-cli                 Установить локальные launchers codex-shared-onboard и codex-shared.
version                     Показать версию инструмента.
self-test                   Запустить test suite скрипта во временных директориях.
```

Флаги команд:

```text
install --apply             Применить install-изменения. Без него только показать план.
install --configure-syncthing
                            Попробовать зарегистрировать .codex-shared в локальном Syncthing через REST API.

doctor --codex             Попросить Codex CLI объяснить captured diagnostics.
doctor --codex-only        Печатать только Codex analysis без raw doctor output.
doctor --codex-read-repo   Разрешить read-only чтение репозитория во время Codex analysis.
doctor --codex-profile P   Передать Codex config profile в codex exec.
doctor --codex-model M     Передать Codex model в codex exec.
doctor --codex-extra-prompt TEXT
                            Добавить дополнительные инструкции в Codex analysis prompt.

snapshot --apply            Инициализировать/использовать Git в .codex-shared и закоммитить текущий shared state.

memories adopt --apply      DEPRECATED: применить writer adoption. Откажется перезаписывать существующую .codex-shared/memories.
memories link --apply       DEPRECATED: применить reader/shared memory linking. Остановится при наличии memory conflict files.
memories publish --apply    Применить copy-based writer publish. Остановится при local memory conflict files.
memories consume --apply    Применить copy-based reader consume. Требует валидный manifest.json.

install-cli --apply         Записать оба launchers. Без него только показать план.
install-cli --bin-dir PATH  Директория для launcher. По умолчанию ~/.local/bin.
install-cli --force         Перезаписать существующий launcher, если его содержимое отличается.
install-cli --no-path-update
                            На Windows не добавлять директорию launcher в user PATH.
```

Примеры dry-run:

```bash
python codex_shared_onboard.py install
python codex_shared_onboard.py doctor --codex
python codex_shared_onboard.py doctor --codex --codex-extra-prompt "Answer in Russian."
python codex_shared_onboard.py memories publish
python codex_shared_onboard.py memories consume
python codex_shared_onboard.py snapshot
```

Примеры применения:

```bash
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories publish --apply
python codex_shared_onboard.py memories consume --apply
python codex_shared_onboard.py snapshot --apply
```

## CLI Launchers

Чтобы установить локальные команды:

```bash
python codex_shared_onboard.py install-cli
python codex_shared_onboard.py install-cli --apply
```

По умолчанию создаются две команды:

```text
~/.local/bin/codex-shared-onboard
~/.local/bin/codex-shared
```

Используйте `codex-shared-onboard` для первичного setup/onboarding машины. Используйте `codex-shared` для ежедневных operator-команд вроде `doctor`, `memories publish` и `memories consume`.

На Windows создаются `.cmd` launchers, а bin-директория добавляется
в user `PATH`. После установки откройте новый терминал.

На Linux/macOS убедитесь, что `~/.local/bin` есть в `PATH`, и затем вызывайте:

```bash
codex-shared-onboard doctor
codex-shared-onboard install --apply
codex-shared doctor
codex-shared memories publish
```

Каждый launcher - это маленький shim, который указывает на этот файл `codex_shared_onboard.py`. Поэтому после обновления checkout установленные команды обычно обновляются автоматически:

```bash
git pull --ff-only
codex-shared version
```

Повторно запускайте `install-cli --apply --force` только если репозиторий был перемещён, изменились target paths launchers или нужно заменить launchers с другим содержимым:

```bash
python codex_shared_onboard.py install-cli --apply --force
```

## Основная Схема

Не синхронизируйте `.codex` целиком.

Синхронизируйте только:

```text
~/.codex-shared
```

Локальный `.codex` получает shared skills через ссылки:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
```

Предпочтительная схема memories теперь copy-based:

```text
Writer:
  ~/.codex/memories -> publish copy -> ~/.codex-shared/memories-published/current

Reader:
  ~/.codex-shared/memories-published/current -> consume copy -> ~/.codex/memories
```

Так активная `~/.codex/memories` остаётся локальной на каждой машине. Published copies включают Codex-owned internals вроде `.git`, `.agents`, `.codex` и `manifest.json` с file hashes.

Legacy memory linking остаётся доступен через `memories adopt` и `memories link`:

```text
~/.codex/memories -> ~/.codex-shared/memories
```

Используйте legacy link на всю директорию только когда этот tradeoff явно принят. На WSL/Linux symlink через `/mnt/c` может вызывать Codex sandbox/bubblewrap проблемы.

## Первая Машина / Writer

На основной машине, где уже есть нужные локальные memories:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories publish
python codex_shared_onboard.py memories publish --apply
```

`memories publish --apply` делает следующее:

- Требует, чтобы локальная `.codex/memories` была реальной директорией, не symlink/junction.
- Останавливается при conflict-like memory files.
- Копирует локальную `.codex/memories` в `.codex-shared/memories-published/current`.
- Пишет `manifest.json` с file sizes и SHA-256 hashes.
- Сохраняет timestamped copy в `.codex-shared/memories-published/snapshots/`.

Для writer-машины в `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = true
```

Одновременно лучше держать только одного writer-а для memories.

## Новая Машина / Reader

На новой машине сначала настройте Syncthing и дождитесь, пока приедет `.codex-shared`, включая `.codex-shared/memories-published/current`.

Затем:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories consume
python codex_shared_onboard.py memories consume --apply
```

`memories consume --apply` делает следующее:

- Проверяет `.codex-shared/memories-published/current/manifest.json`.
- Останавливается при conflict-like files в published memories.
- Если локальная `.codex/memories` существует, сохраняет её в backup.
- Заменяет локальную `.codex/memories` реальной скопированной директорией, не shared link.

Для reader-машины в `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = false
```

Так reader использует memories, но не генерирует новое memory state.

## Legacy Memory Links

`memories adopt` и `memories link` deprecated и оставлены только для существующих схем со ссылкой на всю директорию.

Используйте `memories adopt --apply` на исходном writer-е только если хотите, чтобы `.codex/memories` стала ссылкой на `.codex-shared/memories`.

Используйте `memories link --apply` на reader-е только если `.codex-shared/memories` уже существует и вы явно принимаете symlink/junction модель.

## Syncthing

Скрипт может попробовать зарегистрировать `.codex-shared` в локальном Syncthing через REST API:

```bash
python codex_shared_onboard.py install --configure-syncthing
python codex_shared_onboard.py install --configure-syncthing --apply
```

Это не устанавливает Syncthing и не добавляет remote devices. Устройства и подтверждение sharing всё равно настраиваются в Syncthing UI.

Если API key не найден автоматически:

```bash
python codex_shared_onboard.py --syncthing-api-key YOUR_KEY install --configure-syncthing --apply
```

## Shared Skills

Shared skills лежат тут:

```text
~/.codex-shared/skills-user/<skill-name>/SKILL.md
```

После:

```bash
python codex_shared_onboard.py install --apply
```

они будут доступны локальному Codex через:

```text
~/.codex/skills/<skill-name>
```

Если локальный skill с таким именем уже существует как обычная директория,
`install --apply` сохранит его тут:

```text
~/.codex/skills-backups/<skill-name>.bak-local-YYYYMMDD-HHMMSS
```

Backups лежат вне `~/.codex/skills`, чтобы Codex не регистрировал их как
дубликаты активных skills.

`.system` skills не шарятся. Это локальная часть конкретной установки Codex.

## Snapshot

Можно сделать локальный Git snapshot `.codex-shared`:

```bash
python codex_shared_onboard.py snapshot
python codex_shared_onboard.py snapshot --apply
```

Скрипт не делает push. Snapshot нужен как локальная точка восстановления перед ручными правками или конфликт-резолвом.

## Конфликты Memories

Syncthing не умеет семантически мержить generated memories.

Перед работой с shared memories скрипт ищет conflict-like файлы:

```text
*sync-conflict*
*sync_conflict*
*conflicted copy*
delimiter-separated conflict/conflicted markers
```

Если такие файлы есть:

- Не выбирайте победителя молча.
- Не мержите автоматически `MEMORY.md`, `memory_summary.md`, `raw_memories.md`.
- Сначала сделайте snapshot.
- Затем вручную смотрите diff и решайте, что оставить.

## Восстановление

Если нужно откатиться от consumed memories:

1. Закройте Codex CLI.
2. Переименуйте текущую `.codex/memories` в сторону.
3. Переименуйте нужный backup обратно в `.codex/memories`.

Пример backup:

```text
~/.codex/memories.bak-local-YYYYMMDD-HHMMSS
```

Для legacy linked memories удаляйте именно junction/symlink, а не target `.codex-shared/memories`.

## Важные Ограничения

- Скрипт не синхронизирует `.codex` целиком.
- Скрипт не устанавливает Codex CLI, Python или Syncthing.
- Скрипт не подтверждает remote devices в Syncthing.
- Скрипт не меняет `config.toml` автоматически.
- Скрипт не включает writer/reader режим memories сам.
- Скрипт не мержит memory conflicts.
