# Contributing to ClaudeGate 🚀

Thank you for your interest in contributing to **ClaudeGate**! We welcome bug fixes, documentation improvements, new provider presets, and feature additions.

---

## 🛠️ Local Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Santosh-Prasad-Verma/ClaudeGate.git
   cd ClaudeGate
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

4. **Run the interactive setup:**
   ```bash
   python start_proxy.py --setup
   ```

5. **Start development server:**
   ```bash
   python start_proxy.py
   ```

---

## 🧪 Testing Guidelines

Before submitting a PR:
- Run `python start_proxy.py --test` to verify upstream connection.
- Test both streaming (`stream=true`) and non-streaming modes with Claude Code CLI.
- Ensure no exceptions crash the ASGI streaming loop.

---

## 💡 Adding a New Provider Preset

To contribute a new preset:
1. Create `presets/<provider_name>.env`.
2. Include default recommended `BIG_MODEL`, `MIDDLE_MODEL`, and `SMALL_MODEL`.
3. Add the provider to the interactive wizard in `src/cli.py` and the `README.md` provider table.

---

## 📜 Code of Conduct & Licensing

Please review and adhere to our [Code of Conduct](CODE_OF_CONDUCT.md) when participating in this community.
By contributing, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
