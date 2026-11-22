# Shared validator cases

These files test the two **vector validators** (TypeScript `src/vector-validator.ts`, Python
`apps/api/app/games/tests/vector_validator.py`) against each other. Both are written from
`CONTRACT.md` section 8; both suites load every file here and must give exactly the messages the
contract requires. A case that one language answers differently is drift, and is a finding.

This is **not** the vector directory: `vectors/` holds the files both reducers run, and a malformed
file must never go there. Every `*.json` file here is one case. Each side reads them all, sorted by
name.

## Common keys

| Key           | Meaning                                                                          |
| ------------- | -------------------------------------------------------------------------------- |
| `format`      | The integer 1.                                                                   |
| `kind`        | `"validator"` or `"coverage"`.                                                   |
| `name`        | The file name without `.json` (`validator-...` or `coverage-...`).               |
| `rule`        | The contract rule the case pins (documentation only).                            |
| `description` | What the case shows (documentation only).                                        |

## `kind: "validator"`

```json
{
  "format": 1,
  "kind": "validator",
  "name": "validator-header-kind-invalid-string",
  "rule": "8.3 header kind",
  "description": "text",
  "file": "vec.json",
  "text": "{\"kind\":\"other\", ...}",
  "messages": ["vec.json: kind must be \"reducer\" or \"canonical_json\""]
}
```

- `file` is the base name with `.json` handed to the validator (`name` inside `text` is checked
  against it).
- `text` is the **raw text of a vector file**, as one JSON string. It is raw on purpose: the number
  token and duplicate key rules of 8.3.1 read the text, which a parsed value hides.
- `messages` is the exact list the validator must return, in order, derived by hand from the tables
  of 8.3. An empty list is a well-formed file. An entry is normally a string compared exactly. An
  entry `{"startsWith": "<prefix>"}` is compared by prefix; it is used only for `invalid JSON:
<detail>`, whose detail is the parser's and is not compared (8.3).

### No alternative readings

A case has exactly one `messages` list. There is no `gap` or `alternatives` field: letting one language pass on one reading and the other on another would hide exactly the drift this corpus exists to catch. When the contract does not decide an answer, the contract is fixed first (CONTRACT.md 8) and the case then states the decided messages. The TypeScript suite fails if a case carries `alternatives` or `gap`.

## `kind: "coverage"`

```json
{
  "format": 1,
  "kind": "coverage",
  "name": "coverage-rejected-event-and-later-events-do-not-count",
  "rule": "8.4",
  "description": "text",
  "registry": ["alpha", "beta", "gamma"],
  "files": [{ "file": "v1.json", "text": "{...}" }],
  "messages": ["uncovered event type: beta", "uncovered event type: gamma"]
}
```

- `registry` is the list of event types to cover (a small made-up list, so the rule is tested
  apart from the 29 real types).
- `files` are in-memory vector files (`file` and raw `text`, as above). Each runs through the
  validator first; only reducer vectors with no problem count (8.4).
- `messages` is the exact list `uncovered event type: <type>`, one per uncovered type, in
  `registry` order. The runners' coverage functions (`checkCoverage` in TypeScript,
  `uncovered_event_type_messages` over the valid reducer vectors in Python) must return it.
