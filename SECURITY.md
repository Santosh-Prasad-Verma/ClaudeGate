# Security Policy

## 🔒 Supported Versions

ClaudeGate receives active security updates on the following releases:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

---

## 🛡️ Security Architecture & Privacy Safeguards

ClaudeGate is designed with a defense-in-depth security model to ensure safe local proxying of API keys and LLM prompts:

1. **Localhost Binding by Default**:
   - The server binds strictly to `127.0.0.1` by default, ensuring zero external exposure to local networks or the public internet.
2. **Constant-Time Authentication (`hmac.compare_digest`)**:
   - Client authentication uses constant-time string comparisons to prevent side-channel timing analysis attacks.
3. **Automated Secret & PII Redaction Engine**:
   - When `SANITIZE_SECRETS=true` is enabled, outgoing requests are scrubbed in real-time for GitHub Personal Access Tokens (`ghp_`), AWS Access Keys (`AKIA...`), OpenAI API keys (`sk-...`), and private SSH RSA/Ed25519 blocks.
4. **Rate Limiting & Memory Bounded Queues**:
   - Sliding-window rate limiters and concurrency semaphores prevent CPU exhaustion and denial-of-service from rogue client processes.

---

## 🚨 Reporting a Vulnerability

If you discover a security vulnerability within ClaudeGate, please do **NOT** open a public issue.

Instead, please report security vulnerabilities privately:
1. **GitHub Security Advisory**: Open a private advisory via [GitHub Security Advisories](https://github.com/Santosh-Prasad-Verma/ClaudeGate/security/advisories/new).
2. **Direct Contact**: Contact the maintainer [Santosh Prasad Verma](https://github.com/Santosh-Prasad-Verma).

### What to Include in Your Report:
- A clear description of the vulnerability and potential impact.
- Step-by-step reproduction instructions or a Proof of Concept (PoC) script.
- Any suggested fixes or mitigations.

### Response Timeline:
- **Initial Response**: Within 24-48 hours acknowledging receipt.
- **Triage & Status Update**: Within 72 hours.
- **Fix & Advisory Disclosure**: Coordinated release within 14 days of patch confirmation.
