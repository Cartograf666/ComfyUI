# Viral Video Pipeline — план работ

> Фокус: Viral. Story Video (HTML) и Story I2V — отдельными документами/позже.
> Главная боль: качество на дешёвых дефолтных моделях (poyo) — «так себе». Сильные модели (seedance/kling) пока не тестировались.
> Статус: только планирование, код позже.

## Контекст, который определяет приоритеты

1. **Viral — это I2V-пайплайн.** Видео генерится из стартового кадра → качество ролика на ~70% определяется качеством стартовой картинки и промпта к ней, а не видео-моделью. Значит главный левередж — image-модель + image-промпт + консистентность, а не видео-модель.
2. **Нет центральной конфиг-ноды.** Провайдер/ключ/модель заданы в 14 нодах поштучно (8× `PoyoAIImageNode`, 6× `PoyoAISeedanceVideoNode`). Переключение провайдера = ручная правка 14 мест. Источник ошибок и трения.
3. **AtlasCloud уже работает в Story I2V** (`GlobalAPIConfigNode`, `StoryVideoSettingsNode`, `StoryImageSettingsNode` в `comfyui_story_i2v.py`), там стоят `kling-v2.0` (video) и `flux-1-schnell` (image). Эти модели сильнее дешёвых poyo-дефолтов → пункт «Atlas» и пункт «качество» совпадают.
4. **Много несохранённой работы:** `nodes_scene_parser.py` (+2617 строк), `nodes_poyo_ai.py` (+373) вне гита. Риск потери до начала любых правок.

---

## Phase 0 — Диагностика (делать первым, 1–2 дня)

Цель: разделить, что упирается в модель, а что в пайплайн — до переписывания.

- **V0.1** Заморозить «золотой» тест-кейс: 1 скрипт × 6 сцен, фикс seed, фикс промпты и картинки-якоря.
- **V0.2** A/B-матрица, по одной переменной за раз:
  - image-модель: poyo-дефолт → `flux-1-schnell` (Atlas) → Flux.2 Dev / Qwen-Image (blueprints)
  - video-модель: `seedance-2-fast` → `seedance-2` → `kling-v2.0` (Atlas)
  - video-промпт: текущий `BuildVeoPromptNode` → обогащённый (камера/свет/движение)
- **V0.3** Чеклист на каждый клип: дрейф персонажа, дрейф стиля, артефакты движения, соответствие промпту. Первые два → пайплайн; последние два → модель.
- **Выход:** таблица «переменная → дельта качества», на её основе перепланировать P1/P2.

---

## P0 — AtlasCloud + центральный конфиг (разблокирует и Atlas, и качество)

- **V1.1 — Закоммитить текущую работу.** `nodes_scene_parser.py`, `nodes_poyo_ai.py` — отдельной веткой, до правок.
- **V1.2 — Проверить Atlas end-to-end в нодах Viral.** `PoyoAIImageNode`/`PoyoAISeedanceVideoNode` уже имеют ветку `api_provider == "atlascloud"` (endpoints `api.atlascloud.ai/.../generateImage|generateVideo`, `nodes_poyo_ai.py`). Прогнать реальный вызов из Viral-ветки, убедиться что upload media / poll prediction отрабатывают.
  - *Acceptance:* одна сцена Viral генерит картинку и видео через Atlas без ручной правки кода.
- **V1.3 — Перенести центральный конфиг из Story I2V в Viral.** Добавить `GlobalAPIConfigNode` (+ image/video settings) в `viral_video_pipeline.json`, развести провайдер/ключ/модель из одного места на все 14 нод.
  - *Acceptance:* смена провайдера poyo↔atlascloud и модели — в одном узле, не в 14.
- **V1.4 — Прописать Atlas-модели как доступные пресеты** (kling-v2.0, flux-1-schnell и т.п.) в Viral, чтобы V0.2 можно было гонять переключателем.

---

## P1 — Качество кадра-якоря и промптов (наибольший ROI по качеству)

- **V2.1 — Поднять дефолтную image-модель.** По итогам V0.2 сделать дефолтом сильнейшую из доступных (Flux.2 Dev / Qwen-Image 2512 / flux на Atlas). Блюпринты уже есть в `blueprints/`.
- **V2.2 — Жёсткий инжект style + character-ref в КАЖДЫЙ image-промпт.** Сейчас консистентность держится на том, что Gemini сам повторит описание в тексте (`_repair_image_prompt`, `CharacterRefPromptNode`, `nodes_scene_parser.py`). Это слабо. Инжектить style-anchor и character-ref детерминированно в каждый промпт сцены, а где возможно — через image-edit ноду с ref-картинкой (blueprints: Image Edit Qwen/Flux.2), а не только текстом.
  - *Acceptance:* персонаж/стиль визуально стабильны на всех 6 сценах золотого тест-кейса.
- **V2.3 — Video-промпт под выбранную модель.** `BuildVeoPromptNode` (`nodes_scene_parser.py:3755`) заточен под Veo. Seedance/Kling иначе реагируют на язык движения/камеры. Сделать пресет(ы) промптов под целевую видео-модель, валидировать через Phase 0.
- **V2.4 — Включить FLF/continuity-anchor.** Проверить, реально ли `SceneContinuityAnchorNode` (`nodes_scene_parser.py:3199`) передаёт last-frame предыдущей сцены как якорь следующей (first-last-frame). Если нет — добавить; это крупный левередж связности между сценами.

---

## P2 — Архитектурный долг (скорость итераций → косвенно качество)

- **V3.1 — Мигрировать 6 сцен-ветвей на комбо-ноды.** `ViralSceneImageProcessorNode` / `ViralSceneVideoProcessorNode` / `ViralSceneAudioProcessorNode` (Gen+Cache+Gate) уже написаны (`nodes_scene_parser.py:5193+`). 200 нод → ~50. Меньше трения, быстрее A/B.
- **V3.2 — Вынести общий кэш в один модуль.** `VideoCacheNode`/`AudioCacheNode`/`ImageCacheNode` сейчас тащатся из `scene_parser` в `story_i2v` — связность растёт. Общий модуль кэша на оба пайплайна.
- **V3.3 — Свести провайдер-логику Atlas/poyo в одно место** (`nodes_poyo_ai.py`), чтобы новые модели добавлялись в одной точке.

---

## Порядок исполнения

```
Phase 0 (V0.1–V0.3)  →  P0 (V1.1 commit → V1.2 atlas check → V1.3 central config → V1.4 presets)
                     →  P1 (V2.1 image model → V2.2 ref inject → V2.3 video prompt → V2.4 FLF)
                     →  P2 (миграция на комбо-ноды, рефактор кэша/провайдера)
```

P0 и P1 переплетены через Atlas-модели: центральный конфиг (V1.3) — это то, на чём гоняется матрица Phase 0.
```
