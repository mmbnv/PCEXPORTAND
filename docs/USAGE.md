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

### Дружелюбное приложение (без запоминания команд)

Если не хочется работать через терминал с флагами — есть готовое меню и окно:

```bash
python run.py            # меню: выбрать папку → Analyze → Build APK → отчёт
python run.py --gui      # то же самое, но в отдельном окне (Tkinter)
python -m portagent gui  # открыть окно напрямую
```

В меню/окне можно:

1. Выбрать папку с игрой или установщик (кнопка «Choose folder…» / пункт 1).
2. Нажать **Analyze** — увидеть движок, архитектуру, блокеры и вердикт.
3. Нажать **Build APK** — собрать подписанный APK и посмотреть статистику.
4. Нажать **Run full loop** — прогнать полный цикл (нужен oracle для PASS).
5. Скопировать или сохранить отчёт (**Copy report / Save report…**) и прислать
   его мне — в отчёте уже есть версия Python, какие инструменты найдены и все
   стадии сборки.

Окно требует Tkinter (Debian/Ubuntu: `apt-get install python3-tk`). Само меню
`python run.py` работает и без него.

### Командная строка

```bash
# 1) Разведка: что это за игра и возможен ли порт
python -m portagent analyze /path/to/game

# 2) Полный цикл порта
python -m portagent run /path/to/game --work-dir ./run

# 2b) Сразу все классы стратегий → сводка по всем играм
python -m portagent portall --work-dir ./all

# 3) Посмотреть накопленный опыт
python -m portagent memory --memory ./run/memory.sqlite
```

### Главная команда: `port` (одна команда → APK)

```powershell
python -m portagent port "C:\Games\MyGame" -o MyGame.apk
```

Она делает весь конвейер сразу:
`ANALYZE → CHOOSE → DECOMPILE → TRANSLATE → BUILD → VERIFY`, собирает
**настоящий подписанный APK**, встраивает слой запуска и оптимизирует его под
телефон.

Результат — JSON, где главное поле `apk`:

```json
{
  "status": "ok",
  "engine": "godot",
  "strategy": "godot_android",
  "apk": "MyGame.apk",
  "apk_size": 320615,
  "signed": true,
  "aligned": true,
  "abis": ["arm64-v8a"],
  "assets": {"files": 8, "bytes": 291304},
  "runtime": {"runtime_class": "engine_native", "entry": "GodotGame.exe",
              "backend": "vulkan", "translation": "dxvk"},
  "runtime_bundled": [],
  "decompile": {"status": "ok", "tools_used": ["monodis", "objdump"]},
  "optimization": {"graphics_backend": "vulkan", "heap_mb": 512, "dbt": false},
  "steps": {"runtime": "engine_native: config only (runtime libs not installed)",
            "aapt2_compile": "ok", "aapt2_link": "ok", "javac": "ok",
            "d8": "ok", "native_arm64-v8a": "ok", "assemble": "ok",
            "zipalign": "ok", "sign": "ok"}
}
```

Что внутри APK:

```
AndroidManifest.xml                 ландшафтная активность-лаунчер
classes.dex                         встроенный слой запуска (PortActivity)
lib/arm64-v8a/libportagent.so       собранные NDK-прослойки совместимости
assets/game/**                      вся игра; распаковывается при первом запуске
assets/game/portagent_runtime/      класс запуска + env.json (+ библиотеки)
```

**Класс запуска** выбирается автоматически по результату анализа:

| Класс | Для чего | Что делает |
|---|---|---|
| `engine_native` | Unity / Unreal / Godot / GameMaker / RPG Maker | запускает родной ARM64-рантайм движка на данных игры |
| `mono_managed` | игры на .NET / Mono | поднимает Mono и грузит восстановленные сборки |
| `dbt_box64` | нативные x86/x64 | запускает оригинальный exe через Box64 (DBT) |
| `wine_dxvk` | нативные Windows + D3D | Box64 + Wine + DXVK/VKD3D |

Тяжёлые ARM64-рантаймы (Box64, Wine, DXVK) **не лежат в репозитории**;
`scripts/fetch_runtimes.sh` кладёт их в `~/.portagent-tools/runtime/<класс>/`, и
упаковщик встраивает то, что установлено. Если рантайма нет, приложение прямо
пишет, какой библиотеки не хватает — оно не делает вид, что игра запущена.

**Этап DECOMPILE** реально восстанавливает код: `monodis` даёт IL управляемых
сборок, `objdump` — дизассемблер нативных файлов, `jadx` — исходники DEX,
`Il2CppDumper` — метаданные Unity IL2CPP. Пропустить его можно флагом
`--no-decompile`.

APK проходит `apksigner verify` (схемы v2 и v3) и `zipalign -c`.

**Что это честно делает:** создаёт валидное устанавливаемое приложение,
которое несёт игру, слой совместимости и слой запуска, и оптимизирует его под
телефон. **Чего ещё не делает:** не компилирует восстановленную логику обратно в
нативную ARM64-игру — это следующий этап (связка Decompiler → Translator →
пересборка).

Требуется Android SDK/NDK — поставьте его скриптом из раздела 7.

В качестве `game` можно передать **папку** установленной игры или **один файл**
установщика.

> `/path/to/game` в примерах — это **заглушка**, а не реальный путь. Замените её
> на путь к вашей игре. Если передать её как есть, на Windows получится
> `\path\to\game` и появится ошибка «no such file or directory».

### Команда `portall` — сразу все классы стратегий

```bash
python -m portagent portall --work-dir ./all
```

Одна команда прогоняет весь конвейер по всем встроенным фикстурам (по одному
представителю каждого класса стратегий) и возвращает сводку: для каждой игры —
движок, выбранная стратегия, класс запуска, статус сборки APK и вердикт
(`ok`/`FAIL`). Игра с античитом должна дать `impossible` — это тоже «ok».
Так проверяется, что связка «анализ → декомпиляция → запуск → сборка APK»
работает для каждого класса стратегий. Можно передать свои папки игр вместо
фикстур: `python -m portagent portall /path/to/game1 /path/to/game2`.

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

> **Важно про вывод JSON.** Ключи в отчёте всегда **английские** (`engine`,
> `arch`, `feasible`…). Если в консоли вы видите русские ключи вроде
> «щекотливость» или «осуществимо» — это не PortAgent, а переводчик вашего
> терминала/браузера, который перевёл вывод на лету. Чтобы получить чистый
> отчёт, сохраните его в файл:
>
> ```powershell
> python -m portagent analyze "C:\Games\MyGame" > report.json
> ```
>
> ```bash
> python -m portagent analyze /path/to/game > report.json
> ```
>
> В Windows PowerShell перенаправление по умолчанию пишет в UTF-16; открывайте
> файл в редакторе, который это понимает, либо выполните в cmd.exe.

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

После установки добавьте в окружение (версию NDK посмотрите в
`$ANDROID_HOME/ndk/`):

```bash
export ANDROID_HOME="$HOME/.portagent-tools/android-sdk"
export ANDROID_NDK_HOME="$ANDROID_HOME/ndk/29.0.14206865"
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"
```

Проверить, что всё на месте:

```bash
python -c "from portagent.packager import find_android_toolchain as f; print(f())"
```

Теперь `port` (и `run`) соберут настоящий подписанный APK. Если SDK нет —
сборка помечается `partial`/`fail` и это **не** считается успехом.

Сборка APK идёт **без Gradle и без сети**: напрямую `aapt2 → javac → d8 →
zipalign → apksigner`, поэтому работает даже в офлайн-контейнере.

Проверить готовый APK вручную:

```bash
$ANDROID_HOME/build-tools/34.0.0/apksigner verify --verbose MyGame.apk
$ANDROID_HOME/build-tools/34.0.0/zipalign -c 4 MyGame.apk && echo aligned
```

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

**`invalid choice: 'port' (choose from 'analyze', 'run', 'memory')`.** Вы
запускаете старую копию `portagent` — в ней нет команд `port`/`portall`. Команды
добавлены в ветке `portagent-porter`; переключитесь на неё и обновите файлы:

```bash
git fetch origin
git checkout portagent-porter
git pull
```

Проверьте, что видите новые команды:

```bash
python -m portagent --help        # в списке должны быть port и portall
```

**`FileNotFoundError: 'oracle.json'`.** Вы передали `--oracle oracle.json`, но
файла нет. Уберите флаг — сборка пройдёт, а вердикт будет `UNKNOWN`:

```bash
python -m portagent run /path/to/game --work-dir ./run
```

Либо положите рядом реальный файл с эталонной трассой (см. раздел 6).
Отсутствующий `--oracle` теперь даёт аккуратную ошибку с кодом выхода 2, а не
трейсбек.

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
- `port` собирает **настоящий APK**, но это приложение-контейнер: игра +
  слой совместимости. Автоматическая пересборка произвольной игровой логики из
  машинного кода — следующий этап.
- Полное решение проблемы остановки невозможно; цикл останавливается на
  успехе, жёстком блокере или исчерпании стратегий.
- Динамическая трансляция (DBT) всегда медленнее нативного кода.

Больше деталей — в `README.md`, `docs/ARCHITECTURE.md` и `docs/TOOLCHAIN.md`.
