# X Search — topic → dataset

`api_usage_demo/grok/xsearch.py` turns xAI's `x_search` tool into a data
extractor: give it a topic and a recent window, get back every X post it can
find on that topic — text, images, and video.

Works at any granularity. Broad (`"world cup"`, `"brazil presidential
elections"`) or a single moment (`"xyz getting a red card in the abc match"`) —
the planner decides how wide to fan out.

```bash
python -m api_usage_demo.grok.xsearch "brazil presidential elections" --outdir data/brazil-elections
python -m api_usage_demo.grok.xsearch "world cup" --window 24 --slices 6 --facets 8
python -m api_usage_demo.grok.xsearch "xyz red card" --no-media    # narrow topic, text only
```

## Why it looks like this

`x_search` is an **agentic tool, not a search endpoint**. It has no `limit`, no
cursor, no pagination — one call answers a question and leaves citations behind.
There is no request that means "give me 500 posts".

So recall comes from fan-out. The topic is decomposed into N search facets, the
window is cut into M time slices, and every facet runs against every slice. The
union of all citations, deduped by post id, is the dataset. The prose each call
returns is a byproduct.

Two consequences worth internalising:

- **This is a high-recall sample, not a firehose.** Exhaustive coverage needs X
  API full-archive access. What you get here is broad, not complete.
- **Time slicing is not optional.** Without it the agent returns the same loud
  posts for the whole window and the timeline collapses. Slicing forces
  coverage of quiet periods, which is where most of the signal lives for a
  fast-moving event.

## Pipeline

| stage | what it does |
|-------|--------------|
| `plan` | one tool-free Grok call: topic → search facets (breaking news, official sources, on-the-ground, clips, analysis, contrarian, numbers, reactions) |
| `sweep` | facets × slices, parallel `x_search` calls with structured output |
| `merge` | dedupe by post id; union model rows with citation URLs |
| `hydrate` | X API `/2/tweets?ids=` → exact text, timestamps, metrics, author, media |
| `media` | images → Grok vision; video → targeted `x_search` |
| `emit` | `posts.jsonl` + `manifest.json` |

## Output

- **`posts.jsonl`** — one record per unique post: id, url, handle, which facets
  found it, and model rows carrying `text`, `claim`, `event_type`, `entities`,
  `stance`, `media_description`. After hydration each record also gets `actual`
  with ground-truth text, `created_at`, `public_metrics`, author, and media.
- **`manifest.json`** — per-facet recall, unique vs citation-only counts, failed
  calls, tool invocations, and real billed cost.
- **`sweeps.jsonl`** — raw sweeps appended as they land. A killed run rebuilds
  from this without re-spending; `merge_results` reads the format directly.

## Flags

| flag | effect |
|------|--------|
| `--window` / `--slices` | hours to look back, and how many time buckets to split them into |
| `--facets` | how many distinct search angles to plan |
| `--sweep-images` / `--sweep-videos` | media understanding inline during search — the only video route without X API hydration, and the expensive one |
| `--no-hydrate` | skip X API ground truth (also disables the media pass) |
| `--no-media` / `--no-video` | skip the media pass, or run it images-only |
| `--media-limit` | how many top-engagement posts get media understanding |
| `--checkpoint` | where to stream raw sweeps (defaults to `<outdir>/sweeps.jsonl`) |

## Media understanding

Two tiers, because Grok accepts images directly but **has no direct video
input** — video understanding only exists inside `x_search`.

- **With hydration** (default): media URLs come from the X API, images go
  straight to Grok vision, video routes back through a targeted `x_search`
  scoped to that one post. Selective, so only the top `--media-limit` posts by
  engagement are analysed.
- **Without hydration**: `--sweep-images --sweep-videos` puts understanding
  inline on every search call. It is the only route when you have no X
  credentials, and it is much more expensive.

## Gotchas

- **Hydration is what makes the data trustworthy.** Without `X_BEARER_TOKEN`
  many citations come back as `x.com/i/status/<id>` — real post ids with no
  author attached — and you get no metrics and no verified timestamps. In the
  first Brazil run this was 846 of 1450 posts.
- **Cost is dominated by video.** It is reported exactly from the API's
  `cost_in_usd_ticks`, not estimated. A 48-call sweep with `--sweep-videos` ran
  **$13.33** across 725 actual `x_search` invocations — the agent makes roughly
  15 sub-calls per request, not one.
- **Fabricated posts get caught.** Rows whose URL will not parse to a real post
  id are dropped, and hydration flags any id the X API rejects.
- **Handle restriction kills recall.** `allowed_x_handles` caps a facet at 20
  accounts and the planner is guessing which ones matter. Prefer sweeping
  unrestricted and filtering by verified author at hydration time.

## First dataset

`data/brazil-elections/` — 1450 unique posts over a 24h window, 8 facets × 6
slices, image and video understanding on, 1 failed call out of 48. Event types
self-organised into `polling-numbers`, `announcement`, `investigation`,
`candidate-announcement`, `party-fallout`, `rumour` and others.
