"""Ticket queue handler."""
from .reply import draft_reply


def handle_ticket(ticket):
    reply, ok = draft_reply(ticket["body"])
    return {"ticket_id": ticket["id"], "reply": reply, "needs_human": not ok}
