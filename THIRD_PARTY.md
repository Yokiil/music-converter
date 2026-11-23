# Third-party notices

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

## Separately installed dependencies

- libtakiyasha 2.1.1.post1: MIT; https://github.com/nukemiko/libtakiyasha/blob/2.1.1.post1/LICENSE
- pyaes 1.6.1: MIT; https://github.com/ricmoo/pyaes/blob/master/LICENSE.txt
- Mutagen 1.47.0: GPL-2.0-or-later; https://pypi.org/project/mutagen/1.47.0/
- FFmpeg / ffprobe: the license depends on the independently obtained build;
  https://ffmpeg.org/legal.html

These dependencies, their binaries, and personal server profile files are not
included in this source snapshot. Installing dependencies retains their upstream
license notices in the local dependency directory.
