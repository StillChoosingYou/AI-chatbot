# Third-party files

| File | What it is | Version | License | Source |
|---|---|---|---|---|
| `jsQR.js` | Reads a QR code from a camera frame (used by **Verify ID → Scan QR**). Pure computation: it makes no network requests and touches no storage. | 1.4.0 | Apache-2.0 (`jsQR.LICENSE.txt`) | https://github.com/cozmo/jsQR (npm package `jsqr`) |
| `motion.js` | The animation engine behind Framer Motion, in its plain-JavaScript build (Framer Motion itself needs React; this app does not use it). Used by `static/fx.js` for every animation. It makes no network requests and touches no storage. | 14.6.0 | MIT (`motion.LICENSE.txt`) | https://github.com/motiondivision/motion (npm package `motion`) |
| `fonts/dm-sans-latin-wght-normal.woff2` | The DM Sans typeface (page text), variable weight, Latin letters. | 5.3.0 | SIL Open Font License 1.1 (`fonts/DM-Sans.OFL.txt`) | https://github.com/googlefonts/dm-fonts (npm package `@fontsource-variable/dm-sans`) |
| `fonts/manrope-latin-wght-normal.woff2` | The Manrope typeface (headings and numbers), variable weight, Latin letters. | 5.3.0 | SIL Open Font License 1.1 (`fonts/Manrope.OFL.txt`) | https://github.com/sharanda/manrope (npm package `@fontsource-variable/manrope`) |

Every file is served by this app, so the chatbot looks and works the same with no internet connection. (The page used to
ask Google Fonts for the two typefaces each time it opened; it no longer asks anything outside this app.)

`jsQR.js` is the unmodified `dist/jsQR.js` from the npm tarball `jsqr-1.4.0.tgz`
(sha512 `dxLob7q65Xg2DvstYkRpkYtmKm2sPJ9oFhrhmudT1dZvNFFTlroai3AWSpLey/w5vMcLBXRgOJsbXpdN9HzU/A==`).
It is loaded only when a guard opens the scanner.

`motion.js` is the unmodified `dist/motion.js` (a minified UMD file that defines `window.Motion`) from the npm
tarball `motion-14.6.0.tgz`
(sha512 `qqaZbyubgk7x3zxjPgP1l3Br/KQXol/IIV1uqb358SoN4ApcqNuV7+wyDPg/XLErmuyQL5lL8JVI/fOmDsmBKQ==`;
sha256 of the file `5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf`). It is loaded with the page,
before `fx.js`. If it fails to load, or the visitor asks their system for reduced motion, the page works the same
and nothing moves.

The two fonts are the unmodified `files/dm-sans-latin-wght-normal.woff2` and `files/manrope-latin-wght-normal.woff2`
from the npm tarballs
`fontsource-variable-dm-sans-5.3.0.tgz` (sha512 `BpUG5bqePiDFMBM4ZtLSlPdAIOM990uB1dlXzIf4aw21yQR7BmykedLXXXMqGWsR18aMLOsbWccwJNh3ztTQaA==`;
sha256 of the file `9fea608a947e67020c33cad9a6fe3d60c54119dfb8cff87768a8117a15ed7543`) and
`fontsource-variable-manrope-5.3.0.tgz` (sha512 `6D5dgokHsWDDMtmXHznKa0hK229NN+1a4BLPmUCLqcO1Pw5EEhWY5RFt0AcXnVRAljFFPfRtLkJePQj6LSsV6g==`;
sha256 of the file `a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972`). `static/style.css` declares them
with `@font-face`. A character the Latin files do not contain (such as the peso sign ₱) is drawn by the next font in
the list, the visitor's own system font.

To verify or update any of them:

```
npm pack jsqr@1.4.0      # downloads jsqr-1.4.0.tgz, checks it against the registry
npm pack motion@14.6.0   # downloads motion-14.6.0.tgz, then: tar -xzf motion-14.6.0.tgz package/dist/motion.js
npm pack @fontsource-variable/dm-sans@5.3.0 @fontsource-variable/manrope@5.3.0   # then: tar -xzf <file>.tgz package/files
```
