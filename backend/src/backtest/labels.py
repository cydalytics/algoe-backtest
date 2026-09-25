"""
Backtest Labels

Joins the next-bucket actual and the finished scoreline onto a tick's
selection frame, then settles every row and stamps the workbench
slice dimensions. The live pipeline never sees these columns.

Change Log:
-----------
2026-09-11      Initialize (W06 backtest)
"""

from dataclasses import replace

import pandas as pd

from .dimensions import attach_dimensions
from .settle import settle_row
from .world import label_key


def attach_labels(frame, world) -> None:
    """Add label columns to ``frame.selections`` in place."""
    sel = frame.selections
    if sel.empty:
        return
    sel["label_key"] = [
        label_key(r["match_id"], r["pool_code"], r.get("line_value"), r["selection"])
        for r in sel.to_dict("records")
    ]
    sel["phase"] = sel["match_id"].map(
        lambda mid: frame.contexts[int(mid)].state.phase
        if int(mid) in frame.contexts else ""
    )
    sel["as_of"] = pd.Timestamp(frame.as_of).isoformat()
    sel["day"] = pd.Timestamp(frame.as_of).strftime("%Y-%m-%d")
    sel["minute"] = sel["match_id"].map(
        lambda mid: getattr(frame.contexts[int(mid)].state, "minute", None)
        if int(mid) in frame.contexts else None
    )

    labels = world.labels(frame.as_of)
    finals = world.finals()
    if labels.empty:
        sel["t_actual"] = 0.0
        sel["t_actual_tickets"] = 0
    else:
        keep = labels[["label_key", "t_actual", "t_actual_tickets"]]
        sel = sel.merge(keep, on="label_key", how="left")
        sel["t_actual"] = sel["t_actual"].fillna(0.0)
        sel["t_actual_tickets"] = sel["t_actual_tickets"].fillna(0).astype(int)

    results = []
    for rec in sel.to_dict("records"):
        fin = finals.get(int(rec["match_id"]))
        if not fin:
            results.append(None)
            continue
        results.append(settle_row(
            rec, fin["ft"], fin["ht"], fin["c_ft"], fin["c_ht"],
        ))
    sel["result"] = results
    frame.selections = sel
    attach_dimensions(frame, world)


def copy_frame(frame):
    """A tick frame whose selection table can be mutated per combo."""
    return replace(frame, selections=frame.selections.copy(), warnings=list(frame.warnings))
