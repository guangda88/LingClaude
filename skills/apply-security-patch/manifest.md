---
schema_version: "0.1.0"
skill:
  name: apply-security-patch
  version: "0.1.0"
  owner: lingclaude
  description: "Apply a security patch to a file (e.g. SEC-001 audit gap fix), with AST validation, commit, and end-to-end verification."
interface:
  inputs:
    - name: target_file
      type: string
      required: true
    - name: patch_logic
      type: string
      required: true
      description: "Multi-line python code or description of what to change"
    - name: commit_message
      type: string
      required: true
  outputs:
    - name: trace_id
      type: string
    - name: commit_hash
      type: string
replaceable: warm
dependencies:
  - git@>=2.0
  - python3@>=3.12
tags: [security, audit, sec-001, patch]
---