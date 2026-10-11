# Как пользоваться PortAgent

Это руководство для тех, кто хочет взять папку с ПК-игрой и прогнать её через
PortAgent. Всё описание — по шагам, от простого к сложному.

---

## 1. Что это такое (и чего от него ждать)

PortAgent — это **конвейер**, который проходит цикл:

```
ANALYZE → CHOOSE → EXECUTE → VERIFY → LEARN   (и снова)
```

пока не получится **проверенно эквивалентный** порт либо не будет доказано, что
порт **невозможен**.

Что он делает честно уже сейчас:

- разбирает реальные `PE`/`ELF` бинарники (архитектура, импорты, экспорты);
- определяет движок (Unity, Unreal, Godot, RPG Maker…), графический и звуковой
  API, DRM и анти-чит;
- подбирает стратегию порта и учитывает прошлый опыт;
- генерирует C-прослойку (Win32→POSIX, D3D→Vulkan, звук→AAudio) и собирает её;
- разворачивает настоящий Gradle/NDK-проект;
- проверяет результат на наборе сцен и сравнивает с эталоном;
- честно говорит `UNKNOWN`, если доказать эквивалентность не может.

Чего пока **нет** (важно понимать):

- он не превращает «любую игру» в APK одним нажатием — полная автоматическая
  реконструкция кода это следующий этап;
- без эталона (oracle) проверка вернёт `UNKNOWN`, а не `PASS`;
- сборка подписанного APK требует установленного Android SDK/NDK.

---

## 2. Требования

- Python 3.11+ (только стандартная библиотека, ставить ничего не нужно).
- Для запуска тестов и пилота нужен C-компилятор и кросс-компиляторы:
  ```bash
  sudo apt-get install -y mingw-w64 gcc-aarch64-linux-gnu
  ```
- Для реальной сборки APK — Android SDK/NDK (ставится скриптом, см. раздел 7).

Проверить, что всё на месте:

```bash
python -m portagent --help
```

---

## 3. Быстрый старт (три команды)

```bash
# 1) Разведка: что это за игра и возможен ли порт
python -m portagent analyze /path/to/game

# 2) Полный цикл порта
python -m portagent run /path/to/game --work-dir ./run

# 3) Посмотреть накопленный опыт
python -m portagent memory --memory ./run/memory.sqlite
```

В качестве `game` можно передать **папку** установленной игры или **один файл**
установщика.

> `/path/to/game` в примерах — это **заглушка**, а не реальный путь. Замените её
> на путь к вашей игре. Если передать её как есть, на Windows получится
> `\path\to\game` и появится ошибка «no such file or directory».

### Windows (PowerShell)

Пишите путь к игре в кавычках, можно и с прямыми, и с обратными слэшами:

```powershell
python -m portagent analyze "C:\Games\MyGame"
python -m portagent analyze "D:/Games/MyGame"      # прямые слэши тоже работают
python -m portagent analyze "C:\Games\setup.exe"   # файл установщика
```

Если игра лежит рядом с проектом:

```powershell
python -m portagent analyze ".\MyGame"
```

### Linux / macOS

```bash
python -m portagent analyze "/home/user/Games/MyGame"
```

---

## 4. Команда `analyze` — разведка

Только смотрит и печатает JSON-отчёт. Ничего не меняет и не собирает.

```bash
python -m portagent analyze /path/to/game
```

Пример вывода (сокращённо):

```json
{
  "engine": "unity",
  "engine_confidence": 1.0,
  "engine_evidence": ["UnityPlayer.dll present", "IL2CPP runtime data present"],
  "graphics_api": "d3d11",
  "audio_api": "xaudio",
  "arch": "x86_64",
  "il2cpp": true,
  "managed": false,
  "dependencies": ["d3d11.dll", "kernel32.dll", "xaudio2_9.dll"],
  "drm": [],
  "anticheat": [],
  "blockers": [],
  "feasible": true
}
```

На что смотреть:

| Поле | Значение |
|---|---|
| `engine` | движок: `unity`, `unreal`, `godot`, `rpgmaker`, `custom_native`… |
| `engine_confidence` | уверенность 0…1 |
| `arch` | `x86`, `x86_64`, `arm64` |
| `graphics_api` / `audio_api` | `d3d11`, `opengl`, `xaudio`, `winmm`… |
| `il2cpp` / `managed` | Unity в нативном (IL2CPP) или управляемом (Mono) виде |
| `drm` / `anticheat` | найденные защиты |
| `blockers` | **жёсткие** препятствия (например, анти-чит уровня ядра) |
| `feasible` | итог: возможен ли порт |

**Код возврата** помогает в скриптах:

- `0` — порт возможен;
- `3` — порт невозможен (есть жёсткий блокер).

---

## 5. Команда `run` — полный цикл

```bash
python -m portagent run /path/to/game --work-dir ./run --max-iterations 6
```

Параметры:

| Параметр | По умолчанию | Смысл |
|---|---|---|
| `game` | — | папка игры или файл установщика |
| `--work-dir` | `portagent_run` | куда складывать все артефакты |
| `--memory` | `<work-dir>/memory.sqlite` | база накопленного опыта |
| `--oracle` | нет | JSON с эталонными хешами сцен (см. ниже) |
| `--max-iterations` | `6` | максимум итераций цикла |

В конце печатается итоговый JSON:

```json
{
  "status": "accepted",
  "iterations": 1,
  "strategy": "native_dbt_box64",
  "apk": null,
  "verify": {
    "verdict": "pass",
    "deterministic": true,
    "equivalence_proven": true,
    "checks": {"compiled": true, "apk_built": false, "deterministic": true,
               "scenes_pass": true, "equivalence": true}
  },
  "reports": ["ANALYZE", "CHOOSE", "EXECUTE", "VERIFY", "LEARN", "ACCEPT"]
}
```

Возможные `status`:

| `status` | Что значит | Код возврата |
|---|---|---|
| `accepted` | порт проверен и эквивалентен | `0` |
| `impossible` | доказанная невозможность | `3` |
| `exhausted` | закончились стратегии | `4` |
| `inconclusive` | кончился лимит итераций | `5` |

---

## 6. Про «эталон» (oracle) — самое важное

Чтобы проверить, что порт ведёт себя **как оригинал**, нужен эталон — хеши
трасс трёх сцен: `menu`, `gameplay`, `save`. Это обычный JSON:

```json
{
  "menu": "ad5653d0…",
  "gameplay": "81f5cf20…",
  "save": "9ef0a91c…"
}
```

Правила простые:

- **нет `--oracle`** → вердикт `UNKNOWN`, статус не станет `accepted`;
- **есть `--oracle` и всё совпало** → `PASS`, статус `accepted`;
- **есть `--oracle` и есть расхождения** → `FAIL`, в отчёте список `mismatches`.

Именно поэтому система **никогда не врёт**: без эталона она не объявляет успех.

Готовый эталон можно снять с уже собранной прослойки (так делают тесты и
пилот — см. `scripts/pilot.py`, функция `_oracle`).

---

## 7. Сборка настоящего APK (Android SDK/NDK)

```bash
scripts/bootstrap_android.sh          # JDK + Android SDK + NDK + Gradle
scripts/bootstrap_android.sh --full   # ещё Ghidra, jadx, apktool, Il2CppDumper
```

После установки добавьте в окружение:

```bash
export ANDROID_HOME="$HOME/.portagent-tools/android-sdk"
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/26.1.10909125"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"
```

Теперь `run` сможет собрать `arm64-v8a` и вызвать `gradle assembleDebug`. Если
SDK нет — сборка просто помечается `partial` (собирается только host-версия),
и это **не** считается успехом.

---

## 8. Где что лежит после `run`

```
run/
├── profile.json                 # отчёт ANALYZE
├── strategies_iter1.json        # выбранные стратегии (CHOOSE)
├── reports/                     # JSON-отчёты каждого шага
│   ├── 001_ANALYZE.json
│   ├── 002_CHOOSE.json
│   ├── 003_EXECUTE.json
│   ├── 004_VERIFY.json
│   ├── 005_LEARN.json
│   └── 006_ACCEPT.json
├── iter1_<strategy>/
│   ├── mapping.json             # таблица замен API
│   ├── src/ include/            # сгенерированные C-прослойки
│   ├── native_build/            # объектники (host и/или arm64)
│   ├── android_project/         # Gradle/NDK-проект
│   ├── verify_result.json       # результат VERIFY
│   └── build_result.json        # результат сборки
├── memory.sqlite                # накопленный опыт
└── run_outcome.json             # итог целиком
```

Каждый отчёт шага подчиняется формату:

```json
{
  "step": "ANALYZE|CHOOSE|EXECUTE|VERIFY|LEARN|ACCEPT|IMPOSSIBLE",
  "input": "...",
  "action": "...",
  "artifacts": ["..."],
  "result": "ok|fail|partial",
  "next": "...",
  "memory_update": {}
}
```

---

## 9. Команда `memory` — накопленный опыт

```bash
python -m portagent memory --memory ./run/memory.sqlite
```

Показывает статистику и выученные правила вида «движок + API → стратегия»:

```json
{
  "stats": {"attempts": 14, "passes": 12, "patches": 12, "rules": 7},
  "rules": [
    {"engine": "unity", "graphics_api": "d3d11",
     "strategy": "unity_il2cpp_ndk", "confidence": 0.75,
     "successes": 2, "failures": 0}
  ]
}
```

Чем больше успешных прогонов, тем выше `confidence` — и тем вероятнее, что
планировщик выберет удачную стратегию с первой попытки.

---

## 10. Пилот и тесты

Проверить, что всё работает, можно на наборе синтетических игр (реальные
бинарники собираются на месте):

```bash
python scripts/pilot.py --out pilot     # 7/7 фикстур ведут себя как ожидается
python -m unittest discover -s tests -v # 33 теста
```

Пилот покрывает Unity IL2CPP, Unity Mono, 32-битный D3D11, RPG Maker MV,
Godot, уже-ARM64 ELF и игру с анти-читом уровня ядра (её система обязана
объявить невозможной).

---

## 11. Типовые вопросы

**Порт вернул `inconclusive`.** Не хватило итераций — увеличьте
`--max-iterations` или запустите повторно: правила в `memory.sqlite`
накопятся и выбор стратегии улучшится.

**Порт вернул `impossible`.** Найден жёсткий блокер (например, анти-чит уровня
ядра). Посмотрите `profile.json` → `blockers` для объяснения.

**Хочу воспроизводимость.** Один и тот же `--memory` и один и тот же
`--oracle` дают один и тот же результат; проверка детерминизма встроена в
`VERIFY`.

**Где взять исходники игры?** PortAgent работает с уже установленной игрой
или установщиком. Реверс и распространение портов может нарушать лицензию
(EULA) — это ваша ответственность.

---

## 12. Ограничения (кратко)

- Эквивалентность доказуема только относительно эталона; для всех игр сразу —
  нет (теорема Райса).
- Полная автоматическая реконструкция кода — следующий этап.
- Полное решение проблемы остановки невозможно; цикл останавливается на
  успехе, жёстком блокере или исчерпании стратегий.
- Динамическая трансляция (DBT) всегда медленнее нативного кода.

Больше деталей — в `README.md`, `docs/ARCHITECTURE.md` и `docs/TOOLCHAIN.md`.
