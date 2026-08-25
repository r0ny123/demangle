"""The mangling schemes.

One subpackage per scheme, each exposing a `LanguagePlugin`. `core` never imports any of
them and they never import each other -- both enforced by
`tests/test_architecture.py` -- so a scheme can be developed, replaced, or shipped as a
separate distribution. See `docs/adding-a-scheme.md`.
"""
