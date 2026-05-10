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

## Основная Схема

Не синхронизируйте `.codex` целиком.

Синхронизируйте только:

```text
~/.codex-shared
```

Локальный `.codex` получает shared-части через ссылки:

```text
~/.codex/skills/<skill> -> ~/.codex-shared/skills-user/<skill>
~/.codex/memories      -> ~/.codex-shared/memories
```

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
- Создаёт ссылку или junction `.codex/memories -> .codex-shared/memories`.
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
- Создаёт ссылку или junction `.codex/memories -> .codex-shared/memories`.
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
*conflict*
```

Если такие файлы есть:

- Не выбирайте победителя молча.
- Не мержите автоматически `MEMORY.md`, `memory_summary.md`, `raw_memories.md`.
- Сначала сделайте snapshot.
- Затем вручную смотрите diff и решайте, что оставить.

## Восстановление

Если нужно откатиться от shared memories:

1. Закройте Codex CLI.
2. Удалите junction/symlink `.codex/memories`.
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
