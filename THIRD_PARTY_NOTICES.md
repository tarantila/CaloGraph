# Third-party notices

CaloGraph depends on third-party software. Those components remain under their
own licenses and are not relicensed under CaloGraph's PolyForm Noncommercial
license. The dependency lock files and published SPDX SBOMs provide the full
machine-readable inventory for each release image.

Runtime images also include the license, copyright, and NOTICE files shipped
by their installed production dependencies below `/licenses/backend` or
`/licenses/frontend`. The explicit YAZIO SDK license text is stored
at `/licenses/yazio-sdk-MIT.txt` in the backend image.

## yazio-sdk

CaloGraph pins `yazio-sdk==0.4.0` for its sole direct YAZIO integration.
The generated client is used only behind the CaloGraph provider boundary
and is not exposed as an application API.

- Project: <https://github.com/yazio-community/yazio-sdk-python>
- Package: <https://pypi.org/project/yazio-sdk/>
- Copyright: yazio-community contributors
- License: MIT
- License text: [THIRD_PARTY_LICENSES/yazio-sdk-MIT.txt](THIRD_PARTY_LICENSES/yazio-sdk-MIT.txt)

YAZIO is a trademark of its respective owner. CaloGraph is an independent,
unofficial project and is not affiliated with or endorsed by YAZIO.

## Analysis card loading animation

The three-bar loading animation in `AnalysisHubCard.vue` is adapted from
[aryamitra06's loader on Uiverse](https://uiverse.io/aryamitra06/silent-lion-21).
Its source remains under the following MIT license:

Copyright (c) 2026 aryamitra06 (Aryamitra Chaudhuri)

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
