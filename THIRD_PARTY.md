# Third-party notices

## Project licensing at a glance

| Scope | License |
|---|---|
| Project code (everything except the two files below) | MIT, see [LICENSE](./LICENSE) |
| `core/vendor_readers.py`, `core/mask_reader.py` | LGPL-2.1-or-later (ported from MusicDecrypto) |
| Runtime dependencies installed via pip | their own licenses, listed below |
| Retained test vectors in `tests/test_platform_defaults.py` | LGPL-3.0 (MusicDecrypto test project) |

Each source file states its own scope with an `SPDX-License-Identifier` on line 1.

## NCM format defaults — taurusxin/ncmdump

The NCM server defaults in `core/default_profiles.py` reference the published
`sCoreKey` and `sModifyKey` constants in taurusxin/ncmdump, release **1.5.1**,
revision `e3543a4029a05990921935dab0d14f0bc7907404`:

- Source: https://github.com/taurusxin/ncmdump/blob/e3543a4029a05990921935dab0d14f0bc7907404/src/ncmcrypt.cpp
- License: https://github.com/taurusxin/ncmdump/blob/e3543a4029a05990921935dab0d14f0bc7907404/LICENSE.txt

The values are published format constants, not user credentials. This project
continues to parse and read NCM through its separately installed pinned
libtakiyasha adapter; it does not bundle the ncmdump executable. NCM compatibility
tests construct single-tone audio using the upstream documented file layout,
without importing the production adapter to create its cipher data.

The upstream license is reproduced verbatim below, including its existing
copyright placeholder; no author or year has been inferred to replace it.

```text
MIT License

Copyright (c) [year] [fullname]

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## QMC v1 static mask — Presburger/qmc-decoder

The 128-byte period in `core/default_profiles.py` is derived from the public
seed traversal in [seed.hpp](https://github.com/presburger/qmc-decoder/blob/fb1e04b03246db406e9fc15c4426b79ed321e4f5/src/seed.hpp),
revision `fb1e04b03246db406e9fc15c4426b79ed321e4f5`. Copyright (c) 2019 Presburger.
The MIT notice is retained in [licenses/qmc-decoder-MIT.txt](./licenses/qmc-decoder-MIT.txt).
Its SHA-256 is `749716b1c5f042f65d27b960746ddbce5e04876985d5fc928b0fa1401f7ad096`.
No song or personal client state is included.

## QMC v2, KGM and KWM — MusicDecrypto.Library

Published constants and the Python T2/T3 and KWM reader ports refer to the
original [davidxuang/MusicDecrypto](https://github.com/davidxuang/MusicDecrypto/tree/b97bedc0a88fe827e088e3dafae3fbc4fd73612c)
Library, revision `b97bedc0a88fe827e088e3dafae3fbc4fd73612c`:

- [Tencent/QmcDecrypto.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Tencent/QmcDecrypto.cs): QMCv2 core derivation and ordered EncV2 wrapping constants. The existing libtakiyasha backend performs QMC decryption.
- [Kugou/Decrypto.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/Decrypto.cs), [T2Cipher.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/T2Cipher.cs), [T3Cipher.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kugou/T3Cipher.cs): type 2/3, slot 1 ciphers, signed header layout and fixed slot value.
- [Kuwo/Decrypto.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kuwo/Decrypto.cs) and [Cipher.cs](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/Vendor/Kuwo/Cipher.cs): fixed KWM root, decimal UInt32 seed and repeating mask.

The original Library project explicitly declares **LGPL-2.1-or-later** in its
[project file](https://github.com/davidxuang/MusicDecrypto/blob/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library/MusicDecrypto.Library.csproj),
distinct from the root CLI/GUI license. The original license text accompanies
this source in [licenses/MusicDecrypto-LGPL-2.1.txt](./licenses/MusicDecrypto-LGPL-2.1.txt).
The ports `core/vendor_readers.py` and `core/mask_reader.py` are licensed under
LGPL-2.1-or-later. Changes made 2026-11-23 replace SIMD buffers with Python byte
operations, add input bounds, keep reader ownership with the caller, add the
MIT libtakiyasha-documented UInt64 KWM interpretation, and omit online metadata,
platform client access, encryption APIs and GUI code. These source modules are
included in full and can be modified/replaced independently; no compiled or
obfuscated copy is included. No warranty is offered.

### Public QMC key test vectors

Three small published `.key.enc` / `.key` pairs in `tests/test_platform_defaults.py`
come from the same revision's [Library.Tests/DataSets](https://github.com/davidxuang/MusicDecrypto/tree/b97bedc0a88fe827e088e3dafae3fbc4fd73612c/MusicDecrypto.Library.Tests/DataSets).
They are published test keys, not credentials taken from users or clients, and
are tested offline. That test project's **own** license is LGPL version 3:
[licenses/MusicDecrypto-Tests-LGPL-3.txt](./licenses/MusicDecrypto-Tests-LGPL-3.txt),
with its GPLv3 base text in [licenses/GPL-3.0.txt](./licenses/GPL-3.0.txt). This
specific notice applies to those retained vector bytes; it does not replace the
separate LGPL-2.1-or-later Library notice. Audio used in tests is generated sine
waves, and KGM/KWM media fixtures use independent inverse byte transforms.

## Separately installed dependencies

- libtakiyasha 2.1.1.post1: MIT; https://github.com/nukemiko/libtakiyasha/blob/2.1.1.post1/LICENSE
- pyaes 1.6.1: MIT; https://github.com/ricmoo/pyaes/blob/master/LICENSE.txt
- Mutagen 1.47.0: GPL-2.0-or-later; https://pypi.org/project/mutagen/1.47.0/
- FFmpeg / ffprobe: the license depends on the independently obtained build;
  https://ffmpeg.org/legal.html

These dependencies, their binaries, and personal server profile files are not
included in this source snapshot. Installing dependencies retains their upstream
license notices in the local dependency directory.
