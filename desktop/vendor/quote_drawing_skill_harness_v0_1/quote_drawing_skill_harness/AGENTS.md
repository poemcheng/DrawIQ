# Agent Harness contract
When extracting Chinese quote labels or fastener drawings, load `.agents/skills/quote-drawing-extraction/SKILL.md` first.
Run the deterministic extractor before interpreting documents with an LLM.
Do not invent missing prices, dimensions, OCR text, units, or quotation-to-drawing matches.
A VLM may re-read only unresolved/low-confidence cropped regions, preserving its new evidence, and must not overwrite raw OCR.
Each completed run must produce machine-readable records and a review queue; tests must run before release.
For changes to parsing rules, add a regression fixture and assertion before modifying extraction logic.
