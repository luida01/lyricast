# lyricast/video

Composición Remotion que convierte `sync.json` + `instrumental.wav` + `cover.jpg` en un video karaoke con resaltado palabra a palabra.

## Flujo recomendado: preview en el navegador

`remotion studio` corre en el navegador (GPU) y previsualiza en tiempo real, sin esperar el encode. Es la forma principal de revisar el resultado y ajustar el estilo.

```powershell
cd apps/video
npm run dev          # Remotion Studio: preview en vivo + panel de estilo
```

El panel derecho de Studio edita en vivo: `autoColor`, `color`, `highlightColor`, `background`, `backgroundColor`, `textShadow`.

## Generar el MP4 (cuando lo quieras)

```powershell
npm run render            # MP4 completo (tarda; codificación por software)
npm run render:preview    # tramo corto (primeros 30s) para chequeo rápido
```

La codificación final usa la CPU. Si tu GPU soporta NVENC, Remotion puede acelerarla con
`--hardware-accelerated-encoding` en el comando `render`.

## Preparar una canción

1. Genera los assets con el CLI de la Fase 1:
   ```powershell
   npm run lyricast -- generate "Artista - Canción"
   ```
2. Copia instrumental + portada y genera `src/current-song.ts`:
   ```powershell
   npm run prepare-song -- <slug>
   ```
   `prepare-song` muestrea el color promedio de `cover.jpg` (vía `jpeg-js`) y lo escribe como
   `baseColor`, de modo que el color del karaoke se deriva de la portada de forma determinista
   (igual en studio y en render headless).
3. `npm run dev` para previsualizar.

## Cómo funciona el karaoke

- **Teleprompter rodante**: la vista se desplaza de forma continua siguiendo la línea activa.
  Durante los huecos instrumentales la cámara se desliza suavemente desde la última línea cantada
  hacia la próxima, así nunca se "congela" la pantalla.
- **Resaltado por palabra**: cada palabra se enciende según su `start`/`end` (progresión
  izquierda→derecha dentro del verso).
- **Color automático (`autoColor`)**: toma el color base de la portada, oscurece el fondo, elige
  texto legible por luminancia y un acento **complementario** (rueda de color) con contraste WCAG.
- **Barra de progreso** abajo: avanza siempre con el tiempo (movimiento continuo).
- **♪** durante secciones puramente instrumentales.

## Estructura

- `src/Karaoke.tsx` — composición.
- `src/colors.ts` — utilidades de color (rueda, contraste, muestreo).
- `src/index.tsx` — registro de la composición + schema zod de props.
- `scripts/prepare.ts` — prepara el song (copia assets + color base).
