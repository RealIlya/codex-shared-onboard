# Codex Shared Onboarding

[English version](README.md)

`codex_shared_onboard.py` помогает подключить машину к безопасному shared-слою Codex:

- `.codex-shared` синхронизируется между машинами через Syncthing.
- `.codex` целиком не синхронизируется.
- Пользовательские skills подключаются из `.codex-shared/skills-user` в локальный `.codex/skills`.
- Codex memories можно подключить через `.codex-shared/memories`.
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
doctor                      Проверить paths, tools, shared-файлы, memory layout, conflicts и skill links.
snapshot                    Сделать локальный Git snapshot директории .codex-shared.
memories adopt              Скопировать локальную .codex/memories в .codex-shared/memories и залинковать локальные memories на shared-директорию.
memories link               Подключить локальную .codex/memories к существующей .codex-shared/memories без копирования local reader memories поверх shared memories.
install-cli                 Установить локальный launcher codex-shared-onboard.
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

memories adopt --apply      Применить writer adoption. Откажется перезаписывать существующую .codex-shared/memories.
memories link --apply       Применить reader/shared memory linking. Остановится при наличии memory conflict files.

install-cli --apply         Записать launcher. Без него только показать план.
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
python codex_shared_onboard.py memories link
python codex_shared_onboard.py snapshot
```

Примеры применения:

```bash
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories link --apply
python codex_shared_onboard.py snapshot --apply
```

## CLI Launcher

Чтобы установить локальную команду `codex-shared-onboard`:

```bash
python codex_shared_onboard.py install-cli
python codex_shared_onboard.py install-cli --apply
```

По умолчанию launcher создаётся тут:

```text
~/.local/bin/codex-shared-onboard
```

На Windows создаётся `codex-shared-onboard.cmd`, а bin-директория добавляется
в user `PATH`. После установки откройте новый терминал.

На Linux/macOS убедитесь, что `~/.local/bin` есть в `PATH`, и затем вызывайте:

```bash
codex-shared-onboard doctor
codex-shared-onboard install --apply
```

Launcher - это маленький shim, который указывает на этот файл `codex_shared_onboard.py`. Поэтому после обновления checkout установленная команда обычно обновляется автоматически:

```bash
git pull --ff-only
codex-shared-onboard version
```

Повторно запускайте `install-cli --apply --force` только если репозиторий был перемещён, изменился target path launcher-а или нужно заменить launcher с другим содержимым:

```bash
python codex_shared_onboard.py install-cli --apply --force
```

## Основная Схема

Не синхронизируйте `.codex` целиком.

Синхронизируйте только:

```text
~/.codex-shared
```

Локальный `.codex` получает shared-части через ссылки:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
```

Подключение memories использует ссылку на всю директорию:

```text
Windows:
  ~/.codex/memories -> ~/.codex-shared/memories

WSL/Linux:
  ~/.codex/memories -> ~/.codex-shared/memories
```

Так Codex-owned memory internals вроде `.git`, `.agents` и `.codex` остаются активными внутри локального пути `~/.codex/memories`. На WSL/Linux такая ссылка на всю директорию может вызывать проблемы Codex sandbox/bubblewrap, если shared path проходит через `/mnt/c`. Если это случилось, не удаляйте `.git`; сначала проверьте layout и решите вручную.

## Первая Машина / Writer

На основной машине, где уже есть нужные локальные memories:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories adopt
python codex_shared_onboard.py memories adopt --apply
```

`memories adopt --apply` делает следующее:

- Копирует локальную `.codex/memories` в `.codex-shared/memories`.
- Переименовывает старую локальную `.codex/memories` в backup вида `memories.bak-local-YYYYMMDD-HHMMSS`.
- Создаёт ссылку на всю директорию из `.codex/memories` в `.codex-shared/memories`.
- Не удаляет исходные memories.
- Не перезаписывает существующую `.codex-shared/memories`.

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

На новой машине сначала настройте Syncthing и дождитесь, пока приедет `.codex-shared`, включая `.codex-shared/memories`.

Затем:

```bash
python codex_shared_onboard.py self-test
python codex_shared_onboard.py doctor
python codex_shared_onboard.py install
python codex_shared_onboard.py install --apply
python codex_shared_onboard.py memories link
python codex_shared_onboard.py memories link --apply
```

`memories link --apply` делает следующее:

- Проверяет, что `.codex-shared/memories` уже существует.
- Если локальная `.codex/memories` существует, сохраняет её в backup.
- Создаёт ссылку на всю директорию из `.codex/memories` в `.codex-shared/memories`.
- Не копирует локальные reader-memories поверх shared memories.

Для reader-машины в `~/.codex/config.toml`:

```toml
[features]
memories = true

[memories]
use_memories = true
generate_memories = false
```

Так reader читает shared memories, но не пытается их обновлять.

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

Если нужно откатиться от shared memories:

1. Закройте Codex CLI.
2. Удалите саму junction/symlink `.codex/memories`, а не target `.codex-shared/memories`.
3. Переименуйте нужный backup обратно в `.codex/memories`.

Пример backup:

```text
~/.codex/memories.bak-local-YYYYMMDD-HHMMSS
```

На Windows удаляйте именно junction, а не target `.codex-shared/memories`.

## Важные Ограничения

- Скрипт не синхронизирует `.codex` целиком.
- Скрипт не устанавливает Codex CLI, Python или Syncthing.
- Скрипт не подтверждает remote devices в Syncthing.
- Скрипт не меняет `config.toml` автоматически.
- Скрипт не включает writer/reader режим memories сам.
- Скрипт не мержит memory conflicts.
