# OCR input contract

Preferred input JSON structure:
```json
{
  "document_id": "quote_rule_01",
  "document_type": "quote_label",
  "pages": [{"page":1,"width":1000,"height":1400,"lines":[
    {"text":"報價單號：DQ26004001", "bbox":[30,40,780,80],"confidence":0.97},
    {"text":"規格：7/16-20UNCx25.4", "bbox":[20,200,800,235],"confidence":0.91}
  ]}]
}
```
`bbox` is `[x0,y0,x1,y1]` in input page pixel coordinates; missing bounding boxes are accepted but reviewability degrades. `confidence` must be 0..1. The extractor operates on OCR line objects, not OCR of embedded geometrical symbols. For cloud/local VLM ingestion, write a converter conforming to this schema. Avoid losing reading order: rotate pages before OCR and preserve physical line positions.
