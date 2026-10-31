"""Outbound webhooks (ADR-0048, contract webhooks): signing, URL encryption, the SSRF guard, the
sender and the Slack message format. The policy service's ``control_plane/webhooks.py`` decides
what to send; this package only knows how."""
