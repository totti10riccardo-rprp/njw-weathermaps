// cron-worker: starts the map job on time.
//
// GitHub's own schedule fired hours late (17 Oct 2026: the 09:50/10:20 UTC slots ran at 13:16), so a Cloudflare cron
// (free) asks GitHub to run the job at the forecast release times instead. Each fire only dispatches; the job's first
// step decides whether a new GFS run waits (nothing new = about a minute).
//
// Secret GH_TOKEN: a fine-grained GitHub token for this repository, permission "Actions: read and write", nothing else.
const REPO = "totti10riccardo-rprp/njw-weathermaps";
const WORKFLOW = "maps.yml";

async function dispatch(env) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "njw-weathermaps-cron",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  // 204 = queued. Anything else is logged (wrangler tail) and retried by the next fire.
  console.log(JSON.stringify({ evt: "weathermaps_dispatch", repo: REPO, status: r.status, body: r.status === 204 ? "" : (await r.text()).slice(0, 300) }));
  return r.status;
}

export default {
  async scheduled(_event, env, ctx) { ctx.waitUntil(dispatch(env)); },
};
