"""DosaDash Telegram bot adapter (aiogram, webhook mode).

Hard Rule 10: this service is a thin adapter — it normalizes I/O and renders
inline keyboards. All reasoning lives in apps/ai. Webhook echo lands in a
follow-up Phase 0 PR (aiogram dependency added then).
"""
