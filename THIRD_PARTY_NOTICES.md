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
