import json, glob, os, collections

REG = "tools/offline_task_manager/expert_app/mcp/registries"
DENY = {"sv3d_render", "sv3d_save_operation_trace"}   # tool_policy.py:27
ORDER = ["molstar.v1","molstar.v2","niivue.v1","occt.v1","gltf.v1",
         "bim_coordination.v1","architecture_inspection.v1"]

regs = {os.path.basename(f)[:-5]: json.load(open(f)) for f in glob.glob(f"{REG}/*.json")}
def pub(ts):   return [t for t in ts if t.get("visibility")=="public" and t["name"] not in DENY]
def itn(ts):   return [t for t in ts if not (t.get("visibility")=="public" and t["name"] not in DENY)]

cnt = collections.Counter()
for k in ORDER:
    for t in regs[k]["tools"]: cnt[t["name"]] += 1
total = sum(len(regs[k]["tools"]) for k in ORDER)
pubs  = sum(len(pub(regs[k]["tools"])) for k in ORDER)

L = []
w = L.append
w("# MCP library — the tool surface an expert annotates with")
w("")
w("This is the tool surface of the **expert annotation platform**, per viewer.")
w("If you are about to say \"the question's tools were X\", read the viewer's list")
w("below, not a flat list — a tool is callable only in the viewers that declare it.")
w("")
w("For the agent-side server (`sciencevision-mcp`, *ccmcp*) and how it diverges,")
w("see [Relationship to ccmcp](#relationship-to-ccmcp) at the end.")
w("")
w("## Provenance")
w("")
w("| | |")
w("|---|---|")
w("| registries | `tools/offline_task_manager/expert_app/mcp/registries/*.json` (7 files) |")
w("| source | `origin/prod` @ `6c7e3794` (2026-09-12); `prod` and `dev` are the same commit |")
w("| entries | {total} entry records naming {n} distinct tools |".format(total=total, n=len(cnt)))
w("| expert-visible | {p} of {total} entries |".format(p=pubs, total=total))
w("")
w("The registries are the authority. A count quoted from anywhere else is a")
w("count of something else.")
w("")
w("## How an expert's toolset is decided")
w("")
w("The registry alone does not decide it. `resolve_tool_policy` in")
w("`tools/offline_task_manager/tool_policy.py` builds the set the Functions panel")
w("offers:")
w("")
w("```python")
w("public = [t for t in registry[\"tools\"]")
w("          if t.get(\"visibility\") == \"public\"")
w("          and t[\"name\"] not in _EXPERT_UI_DENYLIST]   # {sv3d_render, sv3d_save_operation_trace}")
w("# then per task: public - tool_policy.forbidden_tools")
w("```")
w("")
w("Two consequences worth holding onto:")
w("")
w("- `visibility: internal` entries are listed in the registry but never reach")
w("  the panel. They are {d} of {total} entries here.".format(d=total-pubs, total=total))
w("- `tool_policy.allowed_tools` **no longer narrows anything** — the registry's")
w("  public set is the only source of what an expert may use. The field is still")
w("  read out of stored payloads so historical bundles keep validating; it is not")
w("  what gated the expert. Use `visibility: internal` or `forbidden_tools` instead.")
w("")
w("Both denylisted names are already `internal` in every registry, so the denylist")
w("removes nothing further: expert-visible == `public` for this revision.")
w("")
w("## The seven viewer families")
w("")
w("| registry | entries | expert-visible | internal | covers |")
w("|---|---:|---:|---:|---|")
COV = {
 "molstar.v1": "molecular structure — legacy surface",
 "molstar.v2": "molecular structure — current",
 "niivue.v1": "volumetric imaging (NIfTI) and masks",
 "occt.v1": "mechanical CAD, B-Rep and parts",
 "gltf.v1": "mesh/glTF scenes",
 "bim_coordination.v1": "multi-model building coordination",
 "architecture_inspection.v1": "single-building inspection",
}
for k in ORDER:
    ts = regs[k]["tools"]
    w("| `{k}` | {n} | {p} | {i} | {c} |".format(k=k, n=len(ts),
      p=len(pub(ts)), i=len(itn(ts)), c=COV[k]))
w("| **all** | **{t}** | **{p}** | **{i}** | |".format(t=total, p=pubs, i=total-pubs))
w("")
w("## Tools that more than one family declares")
w("")
multi = {n: c for n, c in cnt.items() if c > 1}
all7  = [n for n, c in cnt.items() if c == len(ORDER)]
w("**In all seven ({n}):**".format(n=len(all7)))
w("")
for n in sorted(all7):
    vis = {regs[k]["tools"][[t["name"] for t in regs[k]["tools"]].index(n)]["visibility"] for k in ORDER}
    w("- `{n}` — visibility: {v}".format(n=n, v="/".join(sorted(vis))))
w("")
w("So the surface every family shares, and that an expert can actually call, is")
w("just `viewer_zoom` and `sv3d_clear_measurements` — the other two are `internal`.")
w("")
w("**In two to six ({n}):**".format(n=len(multi)-len(all7)))
w("")
w("| tool | families | which |")
w("|---|---:|---|")
for n, c in sorted(multi.items(), key=lambda x: (-x[1], x[0])):
    if c == len(ORDER): continue
    ks = [k for k in ORDER if n in [t["name"] for t in regs[k]["tools"]]]
    w("| `{n}` | {c} | {k} |".format(n=n, c=c, k=", ".join(ks)))
w("")
rest = [n for n, c in cnt.items() if c == 1]
w("The remaining **{n} names are declared by exactly one family** — listed below.".format(n=len(rest)))
w("")
w("## Every family, in full")
w("")
w("`shared` marks a name that more than one family declares.")
w("")
for k in ORDER:
    ts = regs[k]["tools"]
    w("### `{k}` — {n} entries ({p} expert-visible, {i} internal)".format(
        k=k, n=len(ts), p=len(pub(ts)), i=len(itn(ts))))
    w("")
    w("| tool | expert-visible | shared |")
    w("|---|---|---|")
    for t in ts:
        n = t["name"]
        ev = "yes" if (t.get("visibility")=="public" and n not in DENY) else "no"
        sh = "shared" if cnt[n] > 1 else ""
        w("| `{n}` | {e} | {s} |".format(n=n, e=ev, s=sh))
    w("")
w("## Domain and viewer are different axes")
w("")
w("\"Domain\" does not select a toolset -- **viewer** does. The viewer hangs off the")
w("task's assets (`bundle_ingest.py` reads `asset.viewer`), not off its domain, so one")
w("domain can span several families and one family serves several domains.")
w("")
w("Over this repository's own `QA-Gen/tasks/**/*.jsonl`, the pairing actually recorded is:")
w("")
w("| domain | viewer | tasks |")
w("|---|---|---:|")
w("| medical | `niivue` | 2563 |")
w("| chemistry | `molstar` | 834 |")
w("| biology | `molstar` | 641 |")
w("| multi | `molstar` | 131 |")
w("| architecture | `gltf` 11, `cityjson` 9, `pointcloud` 6, `svg` 2 | 28 |")
w("| embodied | `genesis_view` | 4 |")
w("")
w("Two things follow, and both are reasons not to write a domain-level tool list:")
w("")
w("- `architecture` alone spreads across four viewers, so one list for that domain")
w("  would be wrong for at least three of them.")
w("- `cityjson`, `pointcloud`, `svg` and `genesis_view` have **no registry among the")
w("  seven** above -- not every viewer has an MCP surface at all.")
w("")
w("**`mechanical_cad` does not appear in these corpora**, so no viewer is asserted")
w("for it here even though `occt.v1` exists and is presumably what serves it. The")
w("authoritative answer is a join of the platform database's task `domain` against")
w("its `payload_json.viewer`.")
w("")
w("## Relationship to ccmcp")
w("")
w("The agent-side server is a **different surface, not a mirror of this one.** Of")
w("the {n} names above, 9 are reachable under the same name with a compatible call".format(n=len(cnt)))
w("shape, 26 have no ccmcp equivalent at all, and the rest are reachable only via a")
w("renamed, split, folded or partial route. Three same-name traps silently change a")
w("measured number (`sv3d_open_scene`, `sv3d_set_render_mode`, `sv3d_measure_angle`).")
w("")
w("Row-by-row: [`PLATFORM_PARITY.md`](PLATFORM_PARITY.md). What each set of \"the MCP")
w("tools\" counts: [`MCP_TOOLS_IN_USE.md`](MCP_TOOLS_IN_USE.md).")
w("")
w("## Regenerating this file")
w("")
w("The tables are generated from the registries, not hand-maintained:")
w("")
w("```bash")
w("python3 tools/offline_task_manager/expert_app/mcp/render_library_doc.py")
w("```")
w("")
w("A change to a registry that this file does not reflect is a bug in this file.")
w("")

open("docs/mcp/MCP_LIBRARY.md","w").write("\n".join(L))
print("wrote docs/mcp/MCP_LIBRARY.md", len(L), "lines")
