"""Wins: ids, which outcomes make the win log, which make a moment of recognition, and the line shown.

Ruling 7: recognition fires on any accept or any human reply; the win log holds referrals, interviews, offers and
Interested replies only. A said-no, an asked-to-stop and an auto reply are never recognition.
"""

WIN_KINDS = ("accepted", "replied", "referred", "interview", "offer")
REPLY_KINDS = ("unsorted", "interested", "not_now", "said_no", "asked_to_stop")
_RECOGNISED_REPLIES = ("interested", "not_now", "unsorted")


def win_id(touch_id: int, kind: str) -> int:
    """`touch_id * 8 + index(kind)`: one stable number for one outcome of one touch."""
    if kind not in WIN_KINDS:
        raise ValueError(f"{kind!r} is not a win kind")
    if isinstance(touch_id, bool) or not isinstance(touch_id, int) or touch_id < 1:
        raise ValueError(f"touch id {touch_id!r} is not a touch")
    return touch_id * 8 + WIN_KINDS.index(kind)


def decode_win_id(wid: int) -> tuple[int, str]:
    """The touch id and kind a win id was made from. `ValueError` for a number `win_id` could not have made."""
    if isinstance(wid, bool) or not isinstance(wid, int) or wid < 8:
        raise ValueError(f"{wid!r} is not a win id")
    touch_id, index = divmod(wid, 8)
    if index >= len(WIN_KINDS):
        raise ValueError(f"{wid!r} is not a win id")
    return touch_id, WIN_KINDS[index]


def is_log_win(kind: str, reply_kind: str | None) -> bool:
    """Whether the outcome belongs in the win log for good (not only while unseen)."""
    if kind in ("referred", "interview", "offer"):
        return True
    return kind == "replied" and reply_kind == "interested"


def is_recognition(kind: str, reply_kind: str | None) -> bool:
    """Whether the outcome earns a moment of recognition: an accept, or a reply from a person who might help.
    A reply not yet sorted counts (`None` is read as `unsorted`); a said-no or a request to stop does not."""
    if kind == "accepted":
        return True
    return kind == "replied" and (reply_kind or "unsorted") in _RECOGNISED_REPLIES


def win_line(kind: str, person_name: str | None, company_name: str, reply_kind: str | None,
             at_local_hhmm: str) -> str:
    """The sentence the UI shows, ending with the New York time (`"Dana Whitfield accepted your request. 9:41"`).
    A touch with no person (an application) names the company where a person would be."""
    who = (person_name or "").strip() or company_name
    if kind == "accepted":
        text = f"{who} accepted your request."
    elif kind == "replied":
        text = {
            "interested": f"{who} replied and sounds interested.",
            "not_now": f"{who} replied, not right now.",
            "said_no": f"{who} said no.",
            "asked_to_stop": f"{who} asked you to stop.",
        }.get(reply_kind or "unsorted", f"{who} replied.")
    elif kind == "referred":
        text = f"{who} referred you at {company_name}." if (person_name or "").strip() \
            else f"Referral at {company_name}."
    elif kind == "interview":
        text = f"Interview at {company_name}."
    elif kind == "offer":
        text = f"Offer from {company_name}."
    else:
        raise ValueError(f"{kind!r} is not a win kind")
    return f"{text} {at_local_hhmm}" if at_local_hhmm else text
