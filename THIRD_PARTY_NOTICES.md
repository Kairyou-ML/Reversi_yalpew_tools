# Third-party component

This project bundles **Egaroucid for Console 7.8.1 Windows x64 SIMD**, created by Nyanyan.

- Project: https://github.com/Nyanyan/Egaroucid
- Release: https://github.com/Nyanyan/Egaroucid/releases/tag/console_v7.8.1
- License: GNU General Public License v3.0; a copy is stored at `engines/egaroucid/LICENSE`.
- Downloaded asset: `Egaroucid_for_Console_7_8_1_Windows_SIMD.zip`
- Local SHA-256 of the downloaded asset: `52D93E4CDDA165C89CF00BF5F3BBC640BC1C20F080D29804C6F0AE92D3F99CFE`

The hash above records the exact file used for this build. GitHub did not publish an independent digest for the asset, so matching this value alone does not authenticate the publisher.

This project also bundles **Edax 4.6 Windows x86-64-v3 (AVX2)**, created by Richard Delorme and contributors.

- Project: https://github.com/abulmo/edax-reversi
- Release: https://github.com/abulmo/edax-reversi/releases/tag/v4.6
- License: GNU General Public License v3.0; a copy is stored at `engines/edax/LICENSE`.
- Downloaded asset: `edax-4.6-MS-windows-x86.zip`
- Local SHA-256 of the downloaded asset: `400E4A4912B991EB9D23A2405F4C33E151FD8FC61E1568F280471A17E368FCB1`

The x86-64-v3 build is selected because the target Intel Core i5-12400F supports AVX2. `data/eval.dat` must remain next to the bundled engine directory.
