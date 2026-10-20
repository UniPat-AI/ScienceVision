// viewer/shared/docs/docs-mcp.js — compose virtual MCP-tool docs.
//
// Input: tool name (e.g. "compute_volume"). The composer fetches:
//   <docs.base_url>/mcp/tools/<name>.schema.json
//   <docs.base_url>/mcp/tools/<name>.intro.md   (may be 404)
//
// It returns a single markdown string that, when fed through marked.parse,
// looks like:
//   # <name>
//   *<schema.description>*
//   ## When to use            (from intro.md, header copied verbatim)
//   ## Example                (from intro.md)
//   ## Parameters             (from schema.parameters, as a table)
//   ## Returns                (from schema.returns)
//   ## Source                 (link to schema.source if present)
//
// If intro.md is missing the composer prepends a banner ("Intro pending —
// schema only") and skips the When-to-use / Example sections.

(function () {
  'use strict';

  function paramTable(params) {
    if (!params || !params.length) return "_no parameters_";
    const lines = [];
    lines.push("| Name | Type | Required | Description |");
    lines.push("|---|---|---|---|");
    for (const p of params) {
      const req = p.required ? "yes" : "no";
      const desc = (p.description || "").replace(/\|/g, "\\|");
      lines.push("| `" + p.name + "` | `" + p.type + "` | " + req + " | " + desc + " |");
    }
    return lines.join("\n");
  }

  function returnsBlock(ret) {
    if (!ret) return "_unspecified_";
    if (!ret.fields || !ret.fields.length) {
      return "Type: `" + (ret.type || "any") + "`";
    }
    const lines = [];
    lines.push("Type: `" + (ret.type || "object") + "`");
    lines.push("");
    lines.push("| Field | Type | Description |");
    lines.push("|---|---|---|");
    for (const f of ret.fields) {
      const desc = (f.description || "").replace(/\|/g, "\\|");
      lines.push("| `" + f.name + "` | `" + f.type + "` | " + desc + " |");
    }
    return lines.join("\n");
  }

  function sourceLink(schema) {
    if (!schema.source) return "_(no source link)_";
    return "[`" + schema.source + "`](#)";
  }

  async function fetchText(url) {
    const resp = await fetch(url, { cache: 'no-cache' });
    if (!resp.ok) {
      const err = new Error('HTTP ' + resp.status + ' for ' + url);
      err.status = resp.status;
      throw err;
    }
    return resp.text();
  }

  async function fetchJson(url) {
    const text = await fetchText(url);
    return JSON.parse(text);
  }

  async function compose(name) {
    const base = window.Manifest.urls.docs.base_url; // ends with '/'
    const schemaUrl = new URL('mcp/tools/' + name + '.schema.json', base).href;
    const introUrl  = new URL('mcp/tools/' + name + '.intro.md',    base).href;

    let schema;
    try { schema = await fetchJson(schemaUrl); }
    catch (e) {
      throw new Error('Missing schema for MCP tool "' + name + '": ' + e.message);
    }

    let introText = '';
    let introMissing = false;
    try { introText = await fetchText(introUrl); }
    catch (e) {
      if (e.status === 404) introMissing = true;
      else throw e;
    }

    const lines = [];
    lines.push('# ' + name);
    lines.push('');
    lines.push('*' + (schema.description || '') + '*');
    lines.push('');
    if (introMissing) {
      // marked treats blockquotes as <blockquote>; we'll style this banner
      // via the prose flow.
      lines.push('> ⚠ Intro pending — schema only. Add `docs/mcp/tools/'
        + name + '.intro.md` with `## When to use` and `## Example` sections.');
      lines.push('');
    } else {
      // Pass intro through verbatim. Authors are required to start with the
      // "## When to use" heading by Plan 2 convention.
      lines.push(introText.trim());
      lines.push('');
    }
    lines.push('## Parameters');
    lines.push('');
    lines.push(paramTable(schema.parameters));
    lines.push('');
    lines.push('## Returns');
    lines.push('');
    lines.push(returnsBlock(schema.returns));
    lines.push('');
    lines.push('## Source');
    lines.push('');
    lines.push(sourceLink(schema));
    lines.push('');
    return lines.join('\n');
  }

  window.DocsMcp = { compose: compose };
})();
