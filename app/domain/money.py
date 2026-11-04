"""Indian rupee formatting."""


def format_inr(amount: int) -> str:
    """Indian digit grouping: 150000 -> ₹1,50,000."""
    s = str(abs(int(amount)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return ("-" if amount < 0 else "") + "₹" + s
