"""browser package.

Reserved for future phases. See `docs/generalization/ROADMAP.md`:

* Phase 1 -> `application_queue` (queue importer/exporter)
* Phase 2 -> `adapters` (ApplicationAdapter, AdapterRegistry, SiemensAdapter)
* Phase 3 -> `navigator` (PageObserver, ClickableClassifier, SafeExplorer)
* Phase 4 -> `form_engine` (FormSchemaExtractor, FieldMapper, FillEngine)
* Phase 5 -> `interventions` (InterventionStore, AnswerMemory, ReviewBeforeSubmit)
* browser  -> Playwright wrapper used by navigator, form_engine, and adapters

No production behavior is implemented here in the bootstrap phase.
"""

from __future__ import annotations
