# Token usage and cost report

Final full-dataset run: `python3 code/main.py` → `output.csv` (518 requests).

## Summary

The agent uses **no hosted LLM**. Every model runs locally, so no tokens are billed.

| Model / engine | Provider | Where it runs | Used for | Calls in final run | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|---|---|
| Tesseract 5.3.4 LSTM OCR engine (neural network) | Tesseract OCR (open source) | local CPU | Reading the 11 screenshots | 11 images (cached after the first run) | 0 | 0 | $0.00 |
| `paraphrase-multilingual-MiniLM-L12-v2` sentence embeddings (optional) | sentence-transformers (open source) | local CPU | Intent check for requests the multilingual rules cannot classify | 0 (all 518 requests were decided by rules) | 0 | 0 | $0.00 |
| Hosted LLM | — | — | not used | 0 | 0 | 0 | $0.00 |

## Totals

| | Value |
|---|---|
| Model providers | Tesseract OCR, sentence-transformers (both local, open source) |
| LLM calls | 0 |
| Input tokens | 0 |
| Output tokens | 0 |
| Total tokens | 0 |
| Average tokens per request | 0 |
| Estimated total cost | $0.00 |
| Estimated cost per request | $0.00 |
| Wall-clock time (warm OCR cache) | ≈ 1 s for 518 requests |
| Wall-clock time (cold, OCR of 11 images) | ≈ 12 s |

## Why no LLM

The answer is graded on exact resource ids, evidence ids and step order. Those decisions are
logical and temporal (which record, which read, before which cut-off), so they are made by a
deterministic symbolic core. Neural models are used only for perception: reading pixels
(OCR) and, when needed, reading free text in any language (embeddings). Both run offline,
give the same result on every run and cost nothing per request.
