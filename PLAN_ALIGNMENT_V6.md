# Plan de Implementación — Alignment v6

> Estado: pendiente de implementar (2026-08-28).
> Contexto: el pipeline v5 (forced alignment con WhisperX) funciona en general, pero
> la región 140–170s de Golden Gun (out_preview3) se ve mal: líneas comprimidas y
> un hueco de ~10.8s sin palabras.

## Diagnóstico (confirmado con datos)

- **El audio está bien**: source/vocals/instrumental = 251.63s idénticos, sin offset.
- **v5 funciona a nivel palabra**: `timingSource=whisperx-forced`, confianza 0.55,
  708/708 palabras con timestamps fonéticos reales (`estimated=0`).
- **Causa raíz**: la partición *proporcional* de líneas→ventanas ignora el contenido
  real del transcript de la pasada 1. Ejemplo medido en Golden Gun:

  ```text
  seg [144.76-163.33] = líneas 44-49 (6 líneas)   "Joseando...freno"
  seg [163.61-186.22] = líneas 50-58 (9 líneas)   "Este 911...taparme el sol"
  ```

  Pero la partición proporcional asignó:

  ```text
  win [144.76-163.33] → líneas 48-52 (texto 46 palabras; audio tiene 65)
  win [163.61-186.22] → líneas 53-59 (texto 58 palabras; audio tiene 63 y empieza 9s tarde)
  ```

  Como el texto de cada ventana no corresponde al audio que contiene, el alineador
  CTC **comprime** líneas (53-59 en 162.6-176s, a 1.0-1.7s por línea) y deja un
  **hueco** (176-187s) sin palabras; además "Tuve que" se estira 6.7s al inicio de
  la ventana siguiente.

- **Hallazgo adicional**: `_anchor_ranges` (v4) tiene un bug de encadenamiento:
  el prefijo congelado puede apuntar a un estado que el mismo bloque sobrescribe
  después → el bloque 2 ancla **dos rangos** (líneas 21-28 y 29-36). La estructura
  usada como fallback también queda invalidada por esto.

## Cambios a implementar

1. **Corregir el DP de `_anchor_ranges`** (`pipeline/lyricast_pipeline/alignment.py`):
   reescribir como DP 2D `best[bloque][línea_fin]` + `choice[bloque][línea_fin]`.
   - Cada bloque contribuye **exactamente un rango**.
   - El encadenamiento solo se hace contra estados de bloques anteriores
     (imposible corromper la cadena por sobrescritura).
   - Mantener el recorte de bordes (trim) actual.
   - Tests: un bloque no puede producir dos rangos; orden monotónico de bloques.

2. **Construir las ventanas de alineación desde los slots de anchors**
   (reemplaza `_assign_lines_to_windows` / partición proporcional en
   `force_align_lyrics`):
   - Bloques anclados → su span + las líneas de su rango.
   - Líneas entre rangos → slot de hueco de transcripción solo si span > 3s y hay
     actividad vocal (`GAP_MIN_SPAN` 1.0 → 3.0; evita que gaps de 1.1s atrapen
     líneas, como pasó con la línea 44 en el hueco [143.65-144.76]).
   - Líneas antes del primer rango / después del último → bloque primero/último.
   - La partición proporcional queda solo como fallback de bordes.

3. **Subir `ANCHOR_MAX_RUN` de 16 → 24** (`alignment.py`):
   el verso de 67s necesita ~22 líneas (pass-1: 193 palabras ≈ 22 líneas × 8.8
   palabras), no 13 como produce ahora el tope de 16.

4. **Regenerar Golden Gun**:
   - Borrar `out/alvaro-diaz-golden-gun/aligned-lyrics.json`.
   - Ejecutar:
     ```powershell
     pipeline\.venv\Scripts\python.exe pipeline/run_pipeline.py `
       --input out\alvaro-diaz-golden-gun\source.wav `
       --output out\alvaro-diaz-golden-gun `
       --artist "Alvaro Diaz" --title "GOLDEN GUN" --duration 251.56
     ```
   - Validar que cada ventana tenga texto ≈ contenido de audio (comparar palabras
     de la pasada 1 vs palabras de letra asignadas por slot).

5. **Renderizar previews** (en `apps/video`, tras `npm run prepare-song -- alvaro-diaz-golden-gun`):
   ```text
   0–30s      (frames 0-900)
   60–90s     (frames 1800-2700)
   140–170s   (frames 4200-5100)   ← la zona que fallaba
   175–195s   (frames 5250-5850)   ← el hueco de 10.8s
   ```

6. **Tests** (`pipeline/tests/test_alignment.py`):
   - DP 2D: un rango por bloque; cadena limpia; repeticiones.
   - Slots→ventanas: mapeo línea↔span correcto; gaps < 3s no crean slot.
   - Fixture del hook en el hueco de transcripción sigue pasando.

## Criterios de aceptación

- `sync.json` de Golden Gun: hooks 0–25s, verso 1 25–93s, coro 94–138s,
  verso 2 145–186s (líneas 53–58 en ~171–186s, no comprimidas), puente 187–215s,
  outro 216–249s.
- Sin huecos > 3s sin palabras donde el audio tenga voz.
- Ninguna línea con duración < 0.7s para más de 5 palabras (salvo ad-libs).
- Previews de los 4 tramos sin compresión ni huecos visibles.
- 63+ tests Python OK, typecheck de `apps/video` OK.

## Archivos afectados

- `pipeline/lyricast_pipeline/alignment.py` (DP, slots, ventanas, constantes)
- `pipeline/tests/test_alignment.py`
- `out/alvaro-diaz-golden-gun/aligned-lyrics.json` (regenerado)
- `apps/video/` (solo re-render de previews; sin cambios de código salvo que el
  renderer requiera algo, lo cual no se espera)