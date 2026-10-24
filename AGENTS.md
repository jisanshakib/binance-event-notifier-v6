# Bot architecture
- Booster extraction uses its own Gemini REST JSON schema and does not call the announcement formatter, preserving pipeline isolation.
- Map Gemini target_name to the existing internal token key at the extraction boundary, preserving caption and publisher compatibility.
- Validate event types against original post evidence and use conservative local fallbacks when extraction fails, avoiding guessed campaign details.
