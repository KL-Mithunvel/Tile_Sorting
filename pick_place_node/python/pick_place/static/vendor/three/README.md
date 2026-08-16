# Vendored Three.js

`three.module.min.js` and `OrbitControls.js` are vendored from the `three` npm package,
version **0.185.1** (`npm pack three@latest`, 2026-08-11), not loaded from a CDN — see
`documents/project/Pick_Place_Sorting_Subsystem.md` §3 for why (offline/lab-network use,
no runtime dependency on outside connectivity). MIT licensed, license header preserved at
the top of `three.module.min.js`.

To update: `npm pack three@latest`, extract, replace `build/three.module.min.js` and
`examples/jsm/controls/OrbitControls.js` with the new versions, re-test the twin
prototype (view switching + live position updates) before committing.
