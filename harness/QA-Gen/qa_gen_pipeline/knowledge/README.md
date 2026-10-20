# Local Knowledge Fixtures

`knowledge/` contains only local, non-production fixtures for configuration
compiler development. It does not contain an expert-approved knowledge source,
raw seed, task answer, asset identifier, or reviewer conversation.

## Digest Rule

For every fixture, calculate the digest from the `fixture_payload` value only:

```text
sha256(utf8(json.dumps(fixture_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)))
```

The `digest` and the `fixture-subdomain-knowledge:<id>@sha256:<digest>`
reference in `index.yaml` must match that result exactly. Source and viewer
fixtures follow the same rule with their `fixture_payload` field.

## Static Validation

From `QA-Gen/qa_gen_pipeline`, run:

```sh
python scripts/validate_static_config.py
```

The command checks every JSON Schema document, validates the current static
config files, and recomputes each local knowledge fixture digest with
`qagen.contracts.digest()`. JSON Schema cannot enforce unique values across
index entries or prove a resolved filesystem path remains below `knowledge/`;
this command is authoritative for those checks.

`configs/agents/model_routing.yaml` is also checked by this command. It is a
disabled, unverified logical routing declaration: it contains no credential or
endpoint, does not establish model availability or approval, and keeps the
fixture adapter local-only. Its planned schema references require blocked run
eligibility and every route disabled, so this configuration fails closed until
a future runtime binding is deliberately introduced.

## Compile Scope

The biology and chemistry records are eligible only for the explicit
`local_fixture_compile` profile. The medical record demonstrates a blocked
leaf. Every record has `production_approval: false`, no approval receipt, and
`production_eligible: false`; production release profiles must reject them.

## Replacing A Fixture

Do not edit a fixture into a production record. A production snapshot needs a
separate immutable source snapshot, genuine human approval receipt, and a new
index record whose policy is enforced by the implementation. Raw source exports
and approval receipts belong in the private runtime workspace described by the
vNext plan, not in this directory.
