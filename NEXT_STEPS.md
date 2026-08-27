# Next Steps

Status: Phase 1 complete. The CLI produces synced karaoke assets from a song query.

## Current State

- Spotify search (accepts both query orders)
- Album-aware YouTube search with official-channel ranking
- yt-dlp audio download
- Demucs vocal/instrumental separation (NVIDIA CUDA, CPU fallback)
- LRCLIB / Genius lyrics
- WhisperX word-level alignment -> sync.json
- Output layout: out/<slug>/{source,vocals,instrumental}.wav, sync.json, lyrics.txt, cover.jpg, meta.json

## Roadmap

### Phase 2 - Video rendering (Remotion) — DONE (core)
- apps/video: Remotion composition `Karaoke` (word-by-word highlight + spring pop)
- Backgrounds: cover blur, gradient, solid
- Style defaults editable en vivo vía schema zod
- Color automático: muestrea la portada (jpeg-js en prepare) -> fondo oscurecido,
  texto por luminancia, acento complementario con contraste WCAG
- Teleprompter rodante con scroll continuo (glide durante huecos -> nunca se congela)
- Barra de progreso + indicador ♪ en instrumentales
- Flujo browser-first: `npm run dev` (studio, preview en vivo); `npm run render` para MP4
- Verificado: still render de Jung Kook - Still With You sin errores; color base #1e2b44


### Phase 3 - Web editor (Next.js)
- Scaffold apps/web
- Search UI (reuse CLI Spotify/YouTube logic)
- Candidate confirmation UI
- Live preview with @remotion/player
- Style controls (font, colors, background, effects)
- Trigger render; download result

### Phase 4 - Batch and queue
- Job queue for multiple songs
- Resume/cache: skip re-download and re-separation when artifacts exist
- Retry logic on transient failures
- Progress reporting

## Backlog / Improvements

- Alignment quality: transliteration Hangul->romaja implemented (pipeline romanize.py +
  alignment.py). Jung Kook dropped from 98% to ~61% estimated words; remaining filled by
  proportional highlight in the video. Still to tune: lower fuzzy threshold, filter ad-libs,
  prefer LRCLIB enhanced LRC when available.
- Handle empty/instrumental lyrics gracefully
- meta.json: record failure state on pipeline errors
- Device profiles: auto | cuda | cpu (explicit override)
- Experimental: DirectML for integrated GPUs (separate branch)
- Packaging for non-developer users
- More tests: alignment unit tests, e2e smoke test

## Deferred

- Legal review before any commercial distribution
- Server-side GPU workers for a hosted product
