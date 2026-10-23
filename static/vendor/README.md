# Third-party code

| File | What it is | Version | License | Source |
|---|---|---|---|---|
| `jsQR.js` | Reads a QR code from a camera frame (used by **Verify ID → Scan QR**). Pure computation: it makes no network requests and touches no storage. | 1.4.0 | Apache-2.0 (`jsQR.LICENSE.txt`) | https://github.com/cozmo/jsQR (npm package `jsqr`) |

`jsQR.js` is the unmodified `dist/jsQR.js` from the npm tarball `jsqr-1.4.0.tgz`
(sha512 `dxLob7q65Xg2DvstYkRpkYtmKm2sPJ9oFhrhmudT1dZvNFFTlroai3AWSpLey/w5vMcLBXRgOJsbXpdN9HzU/A==`).
It is loaded only when a guard opens the scanner. To verify or update it:

```
npm pack jsqr@1.4.0      # downloads jsqr-1.4.0.tgz, checks it against the registry
```
