# douyin-downloader Claude Guidance

Read `AGENTS.md` for the full project rules.

## Shared Logic With Desktop

- This project shares Python backend logic with `/Users/crimson/codes/douyin/douyin-downloader-desktop`.
- When fixing shared logic in `auth/`, `cli/`, `config/`, `control/`, `core/`, `storage/`, `tools/`, `utils/`, or shared tests, apply the equivalent fix in both projects unless the difference is explicitly desktop-only or CLI-only.
- Before finishing a shared-logic fix, compare the touched shared files against the sibling project and either keep them identical or document the intentional divergence.
