# Codex model discovery

A model picker should reflect the models available from the provider selected in
Codex, including models that Codex does not already know about.

## Language

**Selected connection**:
The provider, endpoint, and authentication selected by a Codex configuration and
any explicit profile or invocation overrides. It is not necessarily the connection
of an already-running session with different overrides.

**Provider inventory**:
The model IDs advertised by the selected connection. It determines which models
belong in that connection's catalog.

**Model metadata**:
Information describing a model's name, capabilities, and behavior. The presence
of an ID in the provider inventory alone does not establish these capabilities.

**Picker catalog**:
The provider inventory enriched with model metadata and picker visibility.

**Generic model**:
A model whose full metadata is unavailable. Its catalog entry uses conservative
compatibility defaults rather than another model's claimed capabilities.

**Picker exclusion**:
A rule that hides a model from selection without removing it from the provider
inventory. General suitability rules and personal preferences are distinct.
