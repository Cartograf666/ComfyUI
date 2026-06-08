# Story I2V Pipeline — план работ

> Фокус: Story I2V (нарративный шорт, 6–8 сцен × ~6с ≈ 40–48с, RU→EN, image-to-video).
> Статус: только планирование, код позже.
> Парный документ: `VIRAL_PIPELINE_PLAN.md`.

## Статус реализации (на 2026-06-08)

Реализовано коммитами `Baseline story I2V planning and workflow` → `Implement Story I2V pipeline plan` → `Finish Story I2V defaults and media polish`.

| Пункт | Статус | Комментарий |
|---|---|---|
| S-1.1 commit baseline | ✅ | в гите |
| S-1.2 регистрация нод | ✅ | все 17 типов графа зарегистрированы; `story_i2v_nodes_test.py`, `validate_story_i2v_with_comfy.py` |
| S-1.3 canonical workflow | ✅ | `build_workflow.py` ↔ JSON синхронны |
| S0.1–S0.4 Phase 0 харнесс | ✅ | `story_i2v_phase0_harness.py` + ledger (свип strength 0.1/0.3/0.5, модели, провайдеры) |
| S1.1 единый провайдер | ✅ | `TEXT_API_PROVIDERS = (poyo, google_official, atlascloud)` |
| S1.2 Atlas-пресеты + дефолты | ✅ | settings: provider=atlascloud, video_model=kling-v2.0, авто-коррекция model↔provider |
| S1.3 судьба I2VTransition | ✅ | удалён из графа; FLF сделан через `image_2` |
| S2.1 grid role + strength | ✅ | дефолт поднят 0.1 → **0.3** (`nodes_scene_parser.py:5218,5922`) |
| S2.2 двойной референс | ✅ | `StoryCharacterSheetPromptNode` (identity) + панель (composition) |
| S2.3 bible инжект | ✅ | через character-sheet reference-image |
| S2.4 negative passthrough | ✅ | проброшен в image/video |
| S3.1 озвучка + timing | ✅ | 8× `ViralSceneAudioProcessorNode` (EdgeTTS) + `StoryNarrationPackNode` → длительность гонит длину видео |
| S3.2 video-промпт под модель | ✅ | `STORY_VIDEO_PROMPT_PRESETS` (kling/seedance/generic) |
| S3.3 FLF-континьюити | ✅ | `image_1`=кадр N, `image_2`=кадр N+1 как last-frame |
| S3.4 переходы/музыка | ✅ | `background_music` + volume 0.08 в `VideoConcat8` (ffmpeg `amix`) |
| P3 конвергенция с Viral | ⬜ | поздняя фаза, не начато |

**⚠️ Единственный содержательный остаток — Phase 0 не выполнен:** 0/36 строк ledger оценены (`human_score=null`, `outputs={}`). То есть `strength=0.3`, `kling-v2.0`, `flux-pro` — это разумные дефолты-догадки, а не измеренный оптимум. Машинерия готова; нужно прогнать золотой тест-кейс и проставить оценки.

**Опц. полиш:** S3.4 — музыка через `amix` на фикс-громкости 0.08, не сайдчейн-дакинг; настоящий ducking при желании отдельной задачей.

## Что это и чем отличается

Story I2V — самый молодой, но **архитектурно самый правильный** из трёх пайплайнов:
- комбо-ноды `Gen+Cache+Gate` (`ViralSceneImageProcessorNode`, `StorySceneVideoProcessorNode`, `StoryboardGridProcessorNode` в `nodes_scene_parser.py`);
- **центральный конфиг** (`GlobalAPIConfigNode`, `StoryVideoSettingsNode`, `StoryImageSettingsNode`, `StoryProjectConfig8Node` в `comfyui_story_i2v.py`) — то, чего нет в Viral;
- **кэш с неймспейсом по проекту** (`_project_cache_name(project_slug, ...)`) — лучше плоского кэша Viral;
- approval-гейты через `ExecutionBlocker` (`StoryScriptApprovalNode`).

**Поток данных (по факту из workflow):**
```
StoryProjectConfig8 (slug + cache_mode)
StoryRuScriptGenerator (RU script: character_bible_ru + 8 сцен: visual/action/camera/narration)
  → StoryScriptApproval (ручная правка + gate, ExecutionBlocker)
  → StoryEnPromptPack (RU→EN: global_style, character_bible_en, negative_prompt,
                       storyboard_grid_prompt, 8×image_prompt, 8×video_prompt)
StoryImageSettings/VideoSettings + StoryImageModelSwitch (central provider/model)
  → StoryboardGridProcessor (1 лист → split 4×2 → 8 панелей)
       └→ ViralSceneImageProcessor ×8  (панель идёт как base/reference, strength 0.1)
            → ImageGate ×8
            → StorySceneVideoProcessor ×8 (I2V: seedance / kling)
                 → VideoConcat8FFmpeg → SaveVideo
I2VTransitionGeneratorNode — присутствует, НО НЕ ПОДКЛЮЧЁН
```

**Ядро отличия от Viral — grid-консистентность:** один storyboard-лист генерит 8 панелей в одной генерации (общий стиль/персонажи), затем каждая панель идёт слабым референсом (`image_prompt_strength=0.1`) в полноразмерную генерацию сцены. Это и есть establishing-plate + двойной референс.

---

## Что сейчас не так (находки из кода/workflow)

1. **Целостность:** ноды размазаны между `comfyui_story_i2v.py` и `nodes_scene_parser.py`, workflow собран генератором `scratch/build_workflow.py`. Легко рассинхронить регистрацию. Несохранённая работа (`nodes_scene_parser.py` +2617, `nodes_poyo_ai.py` +373) вне гита.
2. **Провайдеры рассогласованы:** текстовые ноды — `poyo | google_official`; медиа-процессоры и settings — `poyo | atlascloud`. Atlas уже несёт `kling-v2.0` / `flux`, но enum разный → нельзя переключить «всё на Atlas» одним движением.
3. **Разрешение панелей grid:** панель = 1/8 листа (при 1080×1920 ≈ 540×480). Как I2V-якорь напрямую — мелко. Сейчас используется как слабый референс (strength 0.1) → консистентность может быть слишком слабой, либо панель слишком мелкой. Нужна осознанная стратегия.
4. **Двойной референс не достроен:** `base_scene_image` и `reference_image` оба проброшены в процессоры, но не зафиксировано, что куда идёт (фон-плита vs character-sheet) и в каком порядке генерится.
5. **Озвучки нет вообще.** В script есть `narration_ru` на каждую сцену, но в workflow **нет ни одной TTS/audio-ноды** — нарративный голос нигде не синтезируется. Для «story»-пайплайна это крупный пробел. (Есть только нативный `generate_audio` у seedance — это не нарратор.)
6. **`I2VTransitionGeneratorNode` не подключён** — переходов между сценами по факту нет; склейка встык.
7. **Нет FLF-континьюити:** последний кадр сцены N не передаётся как якорь сцены N+1.

---

## P-1 — Baseline перед экспериментами

Phase 0 нельзя начинать на плавающей базе: иначе кэш, workflow JSON, регистрация нод и провайдеры быстро перестанут быть воспроизводимыми.

- **S-1.1 — Snapshot/commit до любых правок и прогонов.** Зафиксировать `nodes_scene_parser.py`, `nodes_poyo_ai.py`, `comfyui_story_i2v.py`, canonical workflow JSON и сам план. Если полноценный commit откладывается — минимум сохранить diff/patch и список файлов.
- **S-1.2 — Sanity-check регистрации.** До A/B-прогонов поднять ComfyUI и убедиться, что все типы из `story_i2v_pipeline.json` находятся в `NODE_CLASS_MAPPINGS`. Это особенно важно из-за split между `comfyui_story_i2v.py` и `nodes_scene_parser.py`.
- **S-1.3 — Зафиксировать canonical workflow source.** На время Phase 0 явно указать, что является первоисточником: `scratch/build_workflow.py` или JSON. Без этого результаты экспериментов могут относиться к разным версиям графа.

---

## Phase 0 — Диагностика (общий харнесс с Viral)

- **S0.1** Переиспользовать золотой тест-кейс/харнесс из Viral Phase 0, но на 8-сценовой структуре.
- **S0.2** Главная переменная здесь — **механизм консистентности**, а не только модель:
  - `image_prompt_strength` панели-референса: 0.1 → 0.3 → 0.5 (где ломается дрейф vs где убивается композиция)
  - grid как референс vs grid-панель напрямую как keyframe
  - дефолтная image-модель: `gpt-image-2` → `flux-1-dev/pro` (Atlas)
  - video-модель: `seedance-2` → `kling-v2.0` (Atlas)
- **S0.3 — Experiment ledger.** Для каждого прогона вести маленькую таблицу/JSON:
  - `project_slug`, `cache_mode`, `seed`
  - image/video provider + model
  - grid resolution и `panel_strategy` (`reference` | `keyframe`)
  - `image_prompt_strength`
  - включены ли `negative_prompt`, `character_bible_en`, FLF
  - output paths
  - human score: character / background / motion / cuts / narration-readiness
- **S0.4 — Cache-version discipline.** В кэш-ключ или manifest результата включать provider, model, prompt hash, negative prompt hash, `image_prompt_strength`, grid strategy и settings schema version. Иначе смена модели/strength может тихо доставать старые ассеты из project namespace cache.
- **Выход:** какая связка даёт стабильного персонажа на всех 8 сценах без потери качества кадра.
- **Definition of Done:** 8 сцен читаются как один мир; персонаж узнаваем во всех сценах; одежда/лицо не дрейфуют; фон не пересобирается с нуля; нет явных деформаций рук/лица; финальные кадры выглядят как полноценные high-res сцены, а не как увеличенные storyboard-панели.

---

## P0 — Целостность + единый провайдер + Atlas

- **S1.1 — Унифицировать провайдер.** Свести enum к одному набору, включить `atlascloud` и в текстовые ноды (`StoryRuScriptGenerator`, `StoryEnPromptPack` — сейчас `google_official`). Цель: один переключатель провайдера на весь пайплайн.
- **S1.2 — Atlas-модели как пресеты** для Phase 0 (`kling-v2.0`, `flux-1-dev/pro`) — централизованно через settings-ноды.
- **S1.3 — Решить судьбу `I2VTransitionGeneratorNode`:** подключить (см. S3.3) или удалить из workflow, чтобы не вводить в заблуждение.

---

## P1 — Движок консистентности (то, ради чего I2V и затевался)

- **S2.1 — Зафиксировать роль grid.** Рекомендация: grid = «лист консистентности» (низкое разрешение допустимо), а финальный кадр сцены — полноразмерная генерация с панелью-референсом. Поднять `image_prompt_strength` выше 0.1 по итогам S0.2 и сделать дефолтом.
- **S2.2 — Достроить двойной референс / establishing-plate** (проект из памяти). Явно развести:
  - `base_scene_image` = статичная фон-плита/establishing-план на чанк;
  - `reference_image` = character-sheet / identity anchor для персонажей;
  - `storyboard_panel` = composition anchor;
  - `scene_image` = финальный high-res визуальный якорь для видео.
  Зафиксировать порядок генерации: плита → персонажи → сцены. Это прямой левередж стабильности фона И персонажей одновременно.
- **S2.3 — Детерминированный инжект `character_bible_en`** в каждый `scene_N_image_prompt`. `StoryEnPromptPack` его уже отдаёт — убедиться, что он реально конкатенируется в промпт сцены, а не просто выводится наружу.
- **S2.4 — Проброс `negative_prompt`.** Pack генерит anti-deformation negative — проверить, что он доходит до image- и video-генерации (а не теряется).

---

## P2 — Движение, континьюити, звук

- **S3.1 — Нарраторская озвучка и timing model (часть story core).** `narration_ru` генерится, но нигде не звучит. Добавить TTS-ветку (EdgeTTS как в Story Video HTML, либо ElevenLabs как в Viral) + синхрон с клипами в `VideoConcat8FFmpeg` (паддинг/тримминг по длине клипа — паттерн уже есть в Viral). Длительность наррации должна влиять на clip duration / trim / padding, поэтому это не поздняя декоративная фича.
- **S3.2 — Video-промпт под целевую модель.** `video_prompt` из Pack — общий. Завести пресеты под `kling-v2.0` / `seedance-2` (язык камеры/движения у них разный), валидировать в Phase 0.
- **S3.3 — FLF-континьюити между сценами.** Последний кадр сцены N → первый кадр сцены N+1 (first-last-frame). Либо через `I2VTransitionGeneratorNode`, либо отдельным звеном. Убирает «рваные» склейки.
- **S3.4 — Переходы/музыка** в финальной склейке (опционально, по аналогии со Story Final Stitcher).

---

## P3 — Архитектура и конвергенция с Viral

- **S4.1 — Закрепить общий слой процессоров.** `ViralSceneImageProcessorNode` уже шарится между Viral и Story I2V — это точка схождения. Формализовать как общий компонент, на него же мигрировать Viral (см. Viral P2).
- **S4.2 — Общий модуль кэша** (`VideoCacheNode`/`ImageCacheNode`/`AudioCacheNode`) для обоих пайплайнов — сейчас тащится из `scene_parser`.
- **S4.3 — Workflow из `scratch/build_workflow.py`** перенести в поддерживаемый генератор или вести JSON как первоисточник; зафиксировать, что это canonical.

---

## Порядок исполнения

```
P-1 (S-1.1 snapshot/commit → S-1.2 регистрация → S-1.3 canonical workflow)
  → Phase 0 (S0.1–S0.4: подобрать strength/ref-стратегию/модели + ledger/cache discipline)
  → P0 (S1.1 единый провайдер → S1.2 Atlas пресеты → S1.3 transition)
  → P1 (S2.1 роль grid → S2.2 двойной реф/плита → S2.3 bible инжект → S2.4 negative)
  → P2 (S3.1 ОЗВУЧКА/timing → S3.2 video-промпт → S3.3 FLF → S3.4 музыка)
  → P3 (конвергенция с Viral)
```

**Самое недооценённое:** S3.1 (озвучки нет вообще) и S2.1/S2.2 (консистентность — ради неё пайплайн и существует, но настроена на strength 0.1, т.е. почти выключена). Эти два пункта, вероятно, дадут больше, чем смена видео-модели.

**Главный технический риск:** связка `grid panel resolution` + `image_prompt_strength` + cache correctness. Если панель слишком слабая, консистентность почти выключена; если слишком сильная, финальная сцена деградирует в апскейл маленькой storyboard-панели; если кэш не версионирован, A/B-результаты становятся недостоверными.
