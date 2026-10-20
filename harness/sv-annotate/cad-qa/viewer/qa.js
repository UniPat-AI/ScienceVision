// tools/cad_qa_pack/viewer/qa.js
// Viewer HTML lives at <pack>/viewer/; manifest resource paths are stored
// relative to the pack root (see manifest.json §3), so prepend this when
// turning a manifest path into a URL.
const ASSET_BASE = "../";

export function renderQA(record) {
  const body = document.getElementById("qa-body");
  body.innerHTML = "";

  const meta = document.createElement("div");
  meta.innerHTML = `<strong>${record.title || record.id}</strong>` +
    (record.complexity != null ? ` · complexity ${record.complexity}` : "") +
    (record.source_step ? ` · <a href="${ASSET_BASE + encodeURI(record.source_step)}" download>source.step ⬇</a>` : "") +
    (record.source_url ? ` · <a href="${record.source_url}" target="_blank" rel="noopener">GrabCAD ↗</a>` : "");
  body.appendChild(meta);

  (record.questions || []).forEach((q, i) => {
    const card = document.createElement("div");
    card.className = "q";
    const prompt = document.createElement("div");
    prompt.className = "prompt";
    prompt.textContent = `Q${i + 1}. ${q.text}`;
    card.appendChild(prompt);

    const input = document.createElement("input");
    input.type = "text";
    input.placeholder = `your answer (${q.answer_type || "text"})`;
    card.appendChild(input);

    const btn = document.createElement("button");
    btn.textContent = "Show answer";
    const ans = document.createElement("div");
    ans.className = "ans"; ans.hidden = true;
    ans.textContent = `Reference: ${q.answer}`;
    btn.onclick = () => { ans.hidden = !ans.hidden; };
    card.appendChild(btn);
    card.appendChild(ans);
    body.appendChild(card);
  });

  if ((record.views || []).length) {
    const v = document.createElement("div");
    v.className = "views";
    v.innerHTML = "<div>Reference views</div>" +
      record.views.slice(0, 6).map(src => `<img src="${ASSET_BASE + src}" loading="lazy"/>`).join("");
    body.appendChild(v);
  }
}
