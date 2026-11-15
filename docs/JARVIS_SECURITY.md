# OpenJarvis Security & Governance Specification (Mark-LVII)

## 1. Security Philosophy & Principles

OpenJarvis operates as a privileged personal assistant with access to local workstation resources. To guarantee safety and prevent unintended destructive actions, the security architecture follows four foundational principles:

1. **Security Enforced by Backend Code:** Security gates are enforced strictly in deterministic Python/Rust code, never left to LLM prompt instructions or self-policing.
2. **Mandatory Fresh Approval for Sensitive Operations:** Sensitive and irreversible actions always require fresh, explicit human authorization.
3. **Fail-Closed by Default:** Unknown paths, unverified inputs, untrusted domains, or ambiguous parameters fail closed.
4. **Complete Auditability & Reversibility:** Privileged actions are logged to an append-only Action Journal with pre-modification backups enabling automatic compensation and rollback.

---

## 2. 5-Tier Permission Classification Model

Every tool, command, and capability is classified into one of 5 distinct tiers defined in `openjarvis.security.types.PermissionLevel`:

| Tier | Name | Description | Policy & Enforcement | Examples |
|---|---|---|---|---|
| **Tier 1** | `READ_ONLY` | Harmless inspection of public or permitted local information. | Autonomous execution permitted. | Reading docs, inspecting logs, query stats. |
| **Tier 2** | `SAFE_LOCAL` | Low-impact local operations with no persistent modifications. | Autonomous execution within safe sandbox. | `dir`, `git status`, `pytest`, checking devices. |
| **Tier 3** | `REVERSIBLE_LOCAL` | File writes or state mutations that can be fully undone. | Permitted autonomously only if automatic backup snapshot is captured in Action Journal. | Modifying a project file, drafting a report. |
| **Tier 4** | `SENSITIVE` | Privileged operations with significant external, system, or software impact. | **Mandatory fresh explicit approval required.** "Remember all" is strictly blocked. | Executing shell commands (`rm`, `git push`, `pip install`), sending emails/messages, editing credentials. |
| **Tier 5** | `IRREVERSIBLE` | Destructive operations that cannot be undone. | **Mandatory fresh explicit approval with high-risk warning.** | Deleting directories, disk formatting, system shutdown, publishing releases. |

---

## 3. Mandatory Fresh Approval Policy

The user's security requirement is enforced in `src/openjarvis/server/approval_routes.py` and `src/openjarvis/tools/approval_store.py`:

- **Actions Requiring Fresh Approval:**
  - Sending emails, SMS, or chat channel messages.
  - Deleting or overwriting important files.
  - Installing or uninstalling software (`pip`, `npm`, `apt`, `winget`).
  - Changing system or security settings.
  - Executing shell commands outside safe prefixes.
  - Destructive Git operations (`push`, `reset --hard`, `clean -fd`).
  - Publishing or deploying applications.
  - Financial transactions or credential changes.
- **Enforcement Rules:**
  - The UI displays the exact tool name, target path/command, arguments, and consequence preview.
  - Approvals apply strictly to the exact approved command and parameters. Any modification generates a new approval request.
  - The AI model is strictly prohibited from approving its own actions.
  - A denied action will never be silently re-routed to an alternative destructive tool.
  - High-tier and sensitive actions ignore `remember=true`, ensuring that critical operations cannot be permanently auto-approved.

---

## 4. Filesystem Sandboxing & Traversal Defense

Path validation in `src/openjarvis/tools/_paths.py`, `file_read.py`, and `file_write.py`:

- **Safe Default Boundaries:** If no `allowed_dirs` are specified, Jarvis defaults safely to:
  - User configuration directory (`~/.openjarvis/`)
  - Current working directory (project root)
  - System temp directory
  - Explicitly granted folders (`/v1/folder-grants`)
- **Blocked System Directories:**
  Access is unconditionally blocked for:
  - Windows: `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`
  - Unix: `/etc`, `/usr`, `/bin`, `/sbin`, `/boot`, `/dev`
- **Canonicalization & Symlink Escape:**
  All paths are canonicalized (`Path.resolve()`). Symlink targets are inspected to prevent symlink traversal attacks outside allowed directories.
- **Sensitive File Protection:**
  Files containing secrets (`.env`, `*.pem`, `*.key`, `id_rsa`, `credentials.json`) are blocked from reading or writing via `is_sensitive_file()`.

---

## 5. Web Retrieval Security & SSRF Defense

Retrieved web content in `src/openjarvis/research/pipeline.py`:

- **SSRF Prevention (`is_safe_target_url`):**
  - Blocks requests to loopback addresses (`localhost`, `127.0.0.1`, `::1`, `0.0.0.0`).
  - Blocks RFC1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
  - Blocks link-local and cloud metadata addresses (`169.254.169.254`, `metadata.google.internal`).
  - Rejects non-HTTP(S) schemes (`file://`, `ftp://`, `gopher://`).
- **Prompt-Injection Sanitization (`sanitize_web_content`):**
  - Strips dangerous HTML tags (`<script>`, `<style>`, `<system>`, `<instructions>`).
  - Neutralizes known prompt injection payloads (`ignore previous instructions`, `you are now in developer mode`, `<|im_start|>`) by replacing them with `[FILTERED_PROMPT_INJECTION]`.

---

## 6. Action Journal & Rollback Compensation Engine

Located in `src/openjarvis/security/action_journal.py`:

- **Append-Only Action History (`action_records`):**
  Records action ID, tool name, parameters, permission tier, approval ID, target path, backup path, status, and reversibility.
- **Pre-Modification File Snapshots:**
  Before any file write or overwrite occurs, `ActionJournal.create_file_backup()` copies the existing file to `~/.openjarvis/backups/<action_id>_<filename>`.
- **Automated Rollback (`undo_action`):**
  - **Overwritten / Modified File:** Restores the exact pre-modification file from the backup snapshot.
  - **Newly Created File:** Automatically deletes the newly created file.
  - **Irreversible Action:** Fails safely with an explicit error explaining that the operation cannot be reversed.
