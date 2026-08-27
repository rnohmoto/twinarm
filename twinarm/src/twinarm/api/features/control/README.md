# control — placeholder slice

Not implemented yet. This slice will own `GET /ctl?c=<url-encoded JSON>`: `204` on success,
`400` on a malformed body.

The command union is owned by the zod schemas in
[`twinarm-web-ui/src/shared/api/`](../../../../../../twinarm-web-ui/src/shared/api/README.md),
derived from [`descovery/mock/v0/koch_web_panel.py`](../../../../../../descovery/mock/v0/koch_web_panel.py).
Per the web UI's rules, a real implementation must be reconciled there first — do not invent
command shapes here.
