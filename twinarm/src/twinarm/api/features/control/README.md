# control — placeholder slice

Not implemented yet. This slice will own `GET /ctl?c=<url-encoded JSON>`: `204` on success,
`400` on a malformed body.

The command union is owned by the zod schemas in
[`twinarm-web-ui/src/shared/api/`](../../../../../../twinarm-web-ui/src/shared/api/README.md),
derived from the v0 single-pair panel (`discovery/mock/v0/koch_web_panel.py`, in git history; its
multi-pair successor is [`discovery/koch/src/koch/panel.py`](../../../../../../discovery/koch/src/koch/panel.py)).
Per the web UI's rules, a real implementation must be reconciled there first — do not invent
command shapes here.
