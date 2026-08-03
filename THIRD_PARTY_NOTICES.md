# Third-party notices

CaloGraph depends on third-party software. Those components remain under their
own licenses and are not relicensed under CaloGraph's PolyForm Noncommercial
license. The dependency lock files and published SPDX SBOMs provide the full
machine-readable inventory for each release image.

Runtime images also include the license, copyright, and NOTICE files shipped
by their installed production dependencies below `/licenses/backend` or
`/licenses/frontend`. The explicit `yazio-exporter` MIT text is stored at
`/licenses/yazio-exporter-MIT.txt` in the backend image.

## yazio-exporter

CaloGraph installs `yazio-exporter` as an external Python dependency and uses
its public API. CaloGraph does not own or maintain that project and does not
vendor its source code.

- Project: <https://github.com/aleksandr-bogdanov/yazio-exporter>
- Package: <https://pypi.org/project/yazio-exporter/>
- Copyright: Alexander Bogdanov and contributors
- License: MIT
- License text: [THIRD_PARTY_LICENSES/yazio-exporter-MIT.txt](THIRD_PARTY_LICENSES/yazio-exporter-MIT.txt)

YAZIO is a trademark of its respective owner. CaloGraph is an independent,
unofficial project and is not affiliated with or endorsed by YAZIO.
