// Which companies to re-check on this run, and where their postings live.
// Reads dashboard/data.js (the source of truth) and the Cold email allowlist in dashboard/app.js.
const fs = require("fs");
global.window = {};
eval(fs.readFileSync("dashboard/data.js", "utf8"));

const app = fs.readFileSync("dashboard/app.js", "utf8");
const lit = app.match(/const COLD_TECH = new Set\(\(([\s\S]*?)\)\.split\("\|"\)\)/)[1];
const COLD = new Set(eval("(" + lit + ")").split("|"));
const BLOCKED = c => (c.blockers || []).some(b => /ITAR|clearance|citizenship|NRC/i.test(b));

const out = [];
for (const c of window.COMPANIES) {
  const inRange = !c.offLane && (c.sizeClass === "small" || c.sizeClass === "unsure");
  const isCold  = !inRange && COLD.has(c.name);
  if (!(inRange || isCold || c.seed)) continue;
  if (BLOCKED(c)) continue;

  const slugs = {}, urls = [];
  if (c.board && c.board.ats && c.board.token) slugs[c.board.ats] = c.board.token;   // set by the radar import
  for (const r of (c.reqs || [])) {
    if (!r.u) continue;
    let m;
    if      ((m = r.u.match(/jobs\.ashbyhq\.com\/([A-Za-z0-9._-]+)/)))                                         slugs.ashby = slugs.ashby || m[1];
    else if ((m = r.u.match(/(?:job-boards|boards|boards-api)\.greenhouse\.io\/(?:v1\/boards\/)?([A-Za-z0-9._-]+)/))) slugs.greenhouse = slugs.greenhouse || m[1];
    else if ((m = r.u.match(/jobs\.lever\.co\/([A-Za-z0-9._-]+)/)))                                            slugs.lever = slugs.lever || m[1];
    else urls.push({ t: r.t, u: r.u, d: r.d });
  }
  if (!Object.keys(slugs).length && !urls.length) continue;
  out.push({ id: c.id, name: c.name, list: inRange ? "linkedin" : "cold", slugs, urls: urls.slice(0, 6), seed: !!c.seed });
}
fs.writeFileSync("refresh/work/final_targets.json", JSON.stringify(out));
console.log(`targets: ${out.length} (linkedin ${out.filter(t => t.list === "linkedin").length}, cold ${out.filter(t => t.list === "cold").length})`);
