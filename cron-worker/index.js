// cron-worker: starts the map jobs on time (maps.yml = GFS; cams.yml = Copernicus air quality and pollen).
//
// GitHub's own schedule fired hours late (17 Oct 2026: the 09:50/10:20 UTC slots ran at 13:16), so a Cloudflare cron
// (free) asks GitHub to run the job at the forecast release times instead. Each fire only dispatches; the job's first
// step decides whether a new GFS run waits (nothing new = about a minute).
//
// Secret GH_TOKEN: a fine-grained GitHub token for this repository, permission "Actions: read and write", nothing else.
const REPO = "totti10riccardo-rprp/njw-weathermaps";
// CAMS's own fire times (10:30 and 13:30 UTC for the 00 UTC runs, 22:30 for the global 12 UTC run); every other cron is GFS.
const CAMS_CRON = "30 10,13,22 * * *";

async function dispatch(env, workflow) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`, Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "njw-weathermaps-cron",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  // 204 = queued. Anything else is logged (wrangler tail) and retried by the next fire.
  console.log(JSON.stringify({ evt: "weathermaps_dispatch", repo: REPO, workflow, status: r.status, body: r.status === 204 ? "" : (await r.text()).slice(0, 300) }));
  return r.status;
}

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(dispatch(env, event.cron === CAMS_CRON ? "cams.yml" : "maps.yml")); },
};
