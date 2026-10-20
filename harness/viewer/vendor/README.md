# viewer/vendor/

Pinned third-party JavaScript bundles used by `viewer/field3d.html`'s nc
plot pane. These ship with the repo so the viewer runs fully offline / on
internal-only hosts; do **not** swap to a CDN at runtime.

When updating a bundle: re-download the matching version from unpkg
(URL recorded below), update the SHA256, and bump the filename suffix to
the new major version.

| File | Version | License | Source URL | SHA256 | Purpose |
|---|---|---|---|---|---|
| `d3-geo.v3.min.js` | 3.x | ISC | https://unpkg.com/d3-geo@3/dist/d3-geo.min.js | `23e574f1e8d4716d622bce45356a379b66a277162b145f5f147e4c8daefb73d2` | `geoOrthographic` projection + `invert()` for the globe renderer |
| `d3-geo-projection.v4.min.js` | 4.x | ISC | https://unpkg.com/d3-geo-projection@4/dist/d3-geo-projection.min.js | `83136941bcf6f37fb8a9e43318601dfacf02901dc604dec13b815a2cd89c813b` | Extra projections (Mollweide, equirect, Robinson) — reserved for future use |
| `d3-array.v3.min.js` | 3.x | ISC | https://unpkg.com/d3-array@3/dist/d3-array.min.js | `80aa70d0cd17dabddf6d056494ea17926a45a69da8b7850220aace331bad671d` | Transitive dep some d3-geo helpers expect on the global |

All three are UMD bundles that attach to the global `d3` namespace when
loaded via plain `<script>` tag.

---

## Docs SPA libraries (Plan 2)

Added for the docs viewer SPA. All available via plain `<script src>` — no module loader required.

| File | Package | Version | Source | License | SHA-256 |
|---|---|---|---|---|---|
| `marked.min.js` | marked | 14.1.4 | https://www.npmjs.com/package/marked | MIT | 941e9644add31c693fe564215a38dce80f8983321de9099b9beba446fbb0b17c |
| `prism.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | ed5ea2ce218febaea989b0734596bf218694488a4e92b6f107c6536d5675e04b |
| `prism-tomorrow.css` | prismjs | 1.30.0 | https://prismjs.com/themes/prism-tomorrow.css | MIT | 1b15fe2971998a048aebb60f26f6eed76122071db9ef3b995abd003224f52a98 |
| `prism/prism-python.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | ed4385685bcf2d4935c8dbbab4bde16603da1329e092d2bf36c3dadd67e9a85c |
| `prism/prism-bash.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 6260814110e5182f2956e3bd257429548d9dbf2a9b66a63719b26cf9fac966a7 |
| `prism/prism-json.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 956d86baa5ae7ec4106758f354ac2d140bdcd7fc103dece02f73ed12b8d663e4 |
| `prism/prism-javascript.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 0345ea83e12b7b974e953c79a64dea35a40308309449db70b82020fb688ac321 |
| `prism/prism-typescript.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 852f5513bb9ca9db247f86ecfce74acc91c541749d34929157240518fef8152a |
| `prism/prism-yaml.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 719c8e8b8c344dc9de510c729f65ba840b1502a0a8e7e25e2ad19ee715f65c02 |
| `prism/prism-markdown.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | 9f1166a087d9a9ffb3a833f2bccbe00920b55b41ade02a0b3054b7ab5fbc70ea |
| `prism/prism-docker.min.js` | prismjs | 1.30.0 | https://www.npmjs.com/package/prismjs | MIT | a6cc0faa5977a40652f62798a692a5ae171e0380480df3ed056e117597ec52dd |
| `lunr.min.js` | lunr | 2.3.9 | https://www.npmjs.com/package/lunr | MIT | 0c50d9002b85780a842afffb567bb54ede402dae7c6dc5997a018614d8044fc8 |

### Upgrade procedure

1. Update the version in this README's table and in the `curl` commands of Plan 2 Task 1.
2. Run the curl downloads.
3. Re-run `viewer/_vendor_check.html` end-to-end (see Step 8).
4. If breaking changes: open an issue, do not silently upgrade.
