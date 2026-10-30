# Paper

`paper/sciencevision.pdf` is the single copy of the compiled paper:
**ScienceVision: Can AI Observe and Reason Like a Scientist?** (UniPat AI, Elorian AI).

The filename is stable so links from the [repository README](../README.md), the citation section, and
any page outside this repository keep resolving across rebuilds: a new build replaces the bytes, not
the path. GitHub renders the PDF in the browser and offers a download.

| | |
|---|---|
| Status | `placeholder` — a working build, not the camera-ready |
| arXiv | not posted; [`resources/paper.json`](../resources/paper.json) records `"arxiv": null` |
| Pages | 29 |
| Built | 2026-10-30T07:27:24+01:00 (XeLaTeX / xdvipdfmx 0.1) |
| Size | 1,407,201 bytes |
| SHA-256 | `2026aef0ac556c1e02b59a8fc67968460d760f9d9c32ba1774cdbb3d79904d21` |

Open [`sciencevision.pdf`](sciencevision.pdf), or the Paper badge at the top of the README. Nothing
here is generated at build time: what is committed is what a reader downloads.

## Publishing a new build

1. Overwrite `paper/sciencevision.pdf`. Keep the name; do not put a date or version in the filename.
2. Update [`resources/paper.json`](../resources/paper.json): `status`, `arxiv`, `pages`, `built_at`,
   `bytes`, `sha256`. `sha256sum paper/sciencevision.pdf` prints the last one.
3. Commit the two files together. Nothing else needs regenerating.

CI hashes the PDF against the recorded value and checks that `status` and `arxiv` agree
(`scripts/validate_repository.py`). A replaced PDF with a stale record fails validation; that is what
the record is for, not an obstacle to work around. Setting `arxiv` to an identifier and `status` to
`published` is the whole switch from placeholder to posted paper.

## What belongs here

- The compiled PDF and this note. `resources/paper.json` is the machine-readable record; keep the two
  in step.
- LaTeX sources, figures, and review notes stay in the paper workspace. A source tree here would
  drift from the committed PDF, and nothing in this repository would notice.

You may commit a build before the paper is settled, provided it is cleared for the repository's
current visibility. Git history keeps every replaced build, so a draft committed to a public
repository stays readable after the final PDF replaces it.
