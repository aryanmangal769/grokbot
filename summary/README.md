# summary

Camp-analysis JSON → brief video (tweet cards + Imagine host PiP).

## Input

JSON file:

```json
{
  "topic": "",
  "tweet_summary": "",
  "top_tweets": {
    "camp1": {
      "name": "",
      "tweet1": { "url": "", "handle": "", "summary": "" },
      "tweet2": { "url": "", "handle": "", "summary": "" },
      "tweet3": { "url": "", "handle": "", "summary": "" }
    },
    "camp2": {
      "name": "",
      "tweet1": { "url": "", "handle": "", "summary": "" },
      "tweet2": { "url": "", "handle": "", "summary": "" },
      "tweet3": { "url": "", "handle": "", "summary": "" }
    }
  },
  "X_leaning": "",
  "num_tweets": 0
}
```

Tweet `url` is hydrated via X API for real text, likes, avatar, premium badge.

## Output

Under `-o` (default `outputs/summary/run/`):

| File | |
|------|--|
| `summary_imagine.mp4` | Final video |
| `main_camps_30s.mp4` | Cards timeline only |
| `avatar_30s.mp4` | Host (2×15s Imagine) |
| `summary_script.txt` | Narration script |
| `input_hydrated.json` | Input + live X fields |

## Run

```bash
# .env: XAI_API_KEY, X_BEARER_TOKEN
python -m summary.imagine_brief --input path/to/camps.json -o outputs/summary/run
./summary/ask.sh --input path/to/camps.json
```

Flags: `--speed 0.8` (default), `--duration 30`, `--no-hydrate`, `--no-chromakey`.
